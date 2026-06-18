"""
extractor.py
Parse raw LinkedIn posts into structured job records via the configured AI provider.
"""

import json
import re
import time
from http.client import HTTPException
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import google.generativeai as genai
from google.api_core import exceptions as google_exceptions

import config

RATE_LIMIT = 14
MAX_RETRIES = 3
RETRY_DELAY = 5
POST_TEXT_LIMIT = 2500
REQUEST_TIMEOUT = 120

_request_times: list[float] = []


class ProviderAPIError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def _wait_for_rate_limit():
    now = time.time()
    _request_times[:] = [t for t in _request_times if now - t < 60]
    if len(_request_times) >= RATE_LIMIT:
        sleep_for = 60 - (now - _request_times[0]) + 1
        print(f"[extractor] Rate limit pause: {sleep_for:.1f}s")
        time.sleep(sleep_for)
    _request_times.append(time.time())


def _parse_json_response(content: str, *, log_error: bool = True) -> Any | None:
    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.IGNORECASE).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for pattern in (r"\[", r"\{"):
        for match in re.finditer(pattern, cleaned):
            try:
                data, _ = decoder.raw_decode(cleaned[match.start():])
                if isinstance(data, (dict, list)):
                    return data
            except json.JSONDecodeError:
                continue

    if log_error:
        preview = cleaned[:160].replace("\n", " ")
        print(f"[extractor] Parse error: no valid JSON found. Preview: {preview!r}")
    return None


def _normalize_jobs_payload(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("jobs", "results", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _is_relevant_tech_job(data: dict) -> bool:
    if not data.get("is_hiring_post", True):
        return False

    title = (data.get("job_title") or "").lower()
    skills = (data.get("skills") or "").lower()

    tech_keywords = {
        "sde", "software engineer", "software developer", "backend",
        "frontend", "front-end", "full stack", "fullstack", "full-stack",
        "intern", "developer", "engineer", "devops", "cloud", "data engineer",
        "ml engineer", "machine learning", "platform engineer", "mobile",
        "android", "ios", "react", "node", "java", "python", "golang",
        "infrastructure", "site reliability", "sre", "qa engineer",
        "test engineer", "embedded", "firmware", "systems engineer",
    }
    spam_keywords = {
        "hr", "recruiter", "recruitment", "team lead", "sales",
        "marketing", "accounts payable", "bpo", "telecaller", "counselor",
        "business development", "field executive", "insurance",
    }

    is_tech = any(kw in title or kw in skills for kw in tech_keywords)
    is_spam = any(kw in title for kw in spam_keywords)
    return not is_spam and (not title or is_tech)


def _to_sheet_job(data: dict, post: dict) -> dict:
    post_date = post.get("date", "")[:10]
    if not post_date:
        post_date = datetime.now(ZoneInfo(config.TIMEZONE)).date().isoformat()

    return {
        "Post Date":          post_date,
        "Company":            data.get("company") or "",
        "Job Title":          data.get("job_title") or "",
        "Job Type":           data.get("job_type") or "",
        "Location":           data.get("location") or "",
        "Salary / Stipend":   data.get("salary") or "",
        "Apply Link / Email": data.get("apply_link") or "",
        "Skills Required":    data.get("skills") or "",
        "Poster Name":        post.get("poster", ""),
        "LinkedIn Post URL":  post.get("url", ""),
        "Raw Snippet":        post.get("text", "")[:300].replace("\n", " "),
    }


SYSTEM_PROMPT = """
You are a job-post parser for an Indian software engineering job board.
You receive many raw LinkedIn posts in one request.

Return ONLY a valid JSON array. Each array item must be a job object with these exact keys:
{
  "source_index":   integer,
  "company":        string | null,
  "job_title":      string | null,
  "job_type":       string | null,
  "location":       string | null,
  "salary":         string | null,
  "apply_link":     string | null,
  "skills":         string | null,
  "is_hiring_post": boolean
}

Rules:
- Include only genuine software engineering job/internship openings.
- Skip non-hiring posts and HR/recruiter/sales/marketing/BPO/business-development posts.
- source_index must exactly match the input post's source_index.
- company extraction order: @mentions, #hashtags, explicit company phrases.
- If no company is identifiable, use null.
- job_type: one of "Full-Time", "Internship", "Contract", "Freelance".
- location: city name or "Remote" or "Hybrid"; prefer Indian cities.
- Skip any role that is SDE 2, SDE 3, Senior, Lead, Principal, Staff, or above.
- Skip QA, testing, test engineer, support, and operations roles.
- Skip SAP and ServiceNow roles entirely.
- Skip any role requiring more than 2 year of experience.
- Skip roles mentioning experience ranges such as "2-5 years", "3-5 years", "5-10 years", "2+ years", "3+ years", or any range whose upper bound exceeds 2 year.
- salary: only if explicitly stated; null otherwise.
- apply_link: URL or email to apply; null if not present.
- skills: comma-separated; null if not mentioned.
- Return compact JSON only. No markdown, no backticks, no explanation.
- The first character of your response must be "[" and the last character must be "]".
- Do not repeat these instructions.
""".strip()


def _build_batch_prompt(posts: list[dict]) -> str:
    payload = []
    for i, post in enumerate(posts):
        payload.append({
            "source_index": i,
            "poster": post.get("poster", ""),
            "date": post.get("date", "")[:10],
            "url": post.get("url", ""),
            "text": post.get("text", "")[:POST_TEXT_LIMIT],
        })

    return (
        f"{SYSTEM_PROMPT}\n\n"
        "INPUT_POSTS_JSON_START\n"
        f"{json.dumps(payload, ensure_ascii=False)}\n"
        "INPUT_POSTS_JSON_END"
    )

def chunked(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def _post_json(url: str, headers: dict[str, str], payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )

    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        raise ProviderAPIError(
            f"HTTP {e.code}: {body}",
            retryable=e.code == 429 or e.code >= 500,
        ) from e
    except (URLError, TimeoutError, HTTPException) as e:
        raise ProviderAPIError(str(e), retryable=True) from e
    except json.JSONDecodeError as e:
        raise ProviderAPIError("Provider returned invalid response JSON.") from e


def _generate_gemini(prompt: str, provider: dict) -> str:
    genai.configure(api_key=provider["api_key"])
    model = genai.GenerativeModel(provider["model"])
    response = model.generate_content(
        prompt,
        generation_config={
            "max_output_tokens": 4096,
            "temperature": 0,
            "response_mime_type": "application/json",
        },
        request_options={"timeout": REQUEST_TIMEOUT},
    )
    return response.text or ""


def _generate_anthropic(prompt: str, provider: dict) -> str:
    response = _post_json(
        provider["url"],
        {
            "x-api-key": provider["api_key"],
            "anthropic-version": "2023-06-01",
        },
        {
            "model": provider["model"],
            "max_tokens": 4096,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    return "".join(
        block.get("text", "")
        for block in response.get("content", [])
        if block.get("type") == "text"
    )


def _generate_openai_compatible(prompt: str, provider: dict) -> str:
    payload = {
        "model": provider["model"],
        "max_tokens": 4096,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
    }
    if config.AI_PROVIDER == "deepseek":
        payload["thinking"] = {"type": "disabled"}

    response = _post_json(
        provider["url"],
        {"Authorization": f"Bearer {provider['api_key']}"},
        payload,
    )
    choices = response.get("choices") or []
    if not choices:
        return ""
    return choices[0].get("message", {}).get("content") or ""


def _generate_json_payload(prompt: str) -> Any | None:
    provider = config.get_ai_provider_config()
    generators = {
        "gemini": _generate_gemini,
        "anthropic": _generate_anthropic,
        "openrouter": _generate_openai_compatible,
        "deepseek": _generate_openai_compatible,
    }

    content = generators[config.AI_PROVIDER](prompt, provider)
    if not content:
        print(f"[extractor] Empty response from {config.AI_PROVIDER}")
        return None

    return _parse_json_response(content)


def _is_retryable_google_error(error: Exception) -> bool:
    return isinstance(
        error,
        (
            google_exceptions.ResourceExhausted,
            google_exceptions.TooManyRequests,
            google_exceptions.DeadlineExceeded,
            google_exceptions.InternalServerError,
            google_exceptions.ServiceUnavailable,
        ),
    )


def extract_jobs_data(posts: list[dict]) -> list[dict]:
    if not posts:
        return []

    provider = config.get_ai_provider_config()
    print(
        f"[extractor] Provider: {config.AI_PROVIDER} "
        f"(model: {provider['model']})"
    )

    all_jobs = []

    BATCH_SIZE = 10

    for batch_no, batch_posts in enumerate(chunked(posts, BATCH_SIZE), start=1):
        print(
            f"[extractor] Processing batch {batch_no} "
            f"({len(batch_posts)} posts)"
        )

        prompt = _build_batch_prompt(batch_posts)

        jobs_payload = []

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                _wait_for_rate_limit()

                parsed = _generate_json_payload(prompt)

                if parsed is None:
                    break

                jobs_payload = _normalize_jobs_payload(parsed)
                break

            except Exception as e:
                retryable = (
                    isinstance(e, ProviderAPIError) and e.retryable
                ) or _is_retryable_google_error(e)
                if retryable and attempt < MAX_RETRIES:
                    wait = RETRY_DELAY * attempt
                    print(
                        f"[extractor] {config.AI_PROVIDER} retry "
                        f"{attempt}/{MAX_RETRIES} in {wait}s: {e}"
                    )
                    time.sleep(wait)
                    continue

                print(
                    f"[extractor] {config.AI_PROVIDER} batch failed: {e}"
                )
                break

        for item in jobs_payload:
            try:
                source_index = int(item.get("source_index"))
            except (TypeError, ValueError):
                continue

            if source_index < 0 or source_index >= len(batch_posts):
                continue

            if not _is_relevant_tech_job(item):
                continue

            all_jobs.append(
                _to_sheet_job(item, batch_posts[source_index])
            )

    print(
        f"[extractor] Extracted {len(all_jobs)} jobs "
        f"from {len(posts)} posts"
    )

    return all_jobs
