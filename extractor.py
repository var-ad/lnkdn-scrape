"""
extractor.py
Parse raw LinkedIn posts into structured job records via Gemini.
"""

import json
import re
import time
from typing import Any

import google.generativeai as genai
from google.api_core import exceptions as google_exceptions

import config

genai.configure(api_key=config.GEMINI_API_KEY)

MODEL = "gemini-3.1-flash-lite"   # good for structured extraction, but can fallback to 2.0 if it fails to parse JSON
FALLBACK_MODEL = "gemini-2.0-flash"

RATE_LIMIT = 14        # stay under Gemini free-tier 15 req/min with buffer
MAX_RETRIES = 3
RETRY_DELAY = 5        # seconds between retries on 429
POST_TEXT_LIMIT = 2500

_request_times: list[float] = []


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
    return {
        "Post Date":          post.get("date", "")[:10],
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


def _generate_json_payload(prompt: str) -> Any | None:
    model_names = [MODEL]
    if FALLBACK_MODEL and FALLBACK_MODEL not in model_names:
        model_names.append(FALLBACK_MODEL)

    for model_name in model_names:
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(
            prompt,
            generation_config={
                "max_output_tokens": 8192,
                "temperature": 0,
                "response_mime_type": "application/json",
            },
        )
        content = response.text
        if not content:
            print(f"[extractor] Empty Gemini batch response from {model_name}")
            continue

        parsed = _parse_json_response(content, log_error=(model_name == model_names[-1]))
        if parsed is not None:
            if model_name != MODEL:
                print(f"[extractor] Fallback model {model_name} returned valid JSON")
            return parsed

        preview = content.strip()[:120].replace("\n", " ")
        print(f"[extractor] {model_name} returned non-JSON, trying fallback. Preview: {preview!r}")

    return None


def extract_jobs_data(posts: list[dict]) -> list[dict]:
    if not posts:
        return []

    prompt = _build_batch_prompt(posts)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            _wait_for_rate_limit()

            parsed = _generate_json_payload(prompt)
            if parsed is None:
                return []
            jobs_payload = _normalize_jobs_payload(parsed)
            break

        except (google_exceptions.ResourceExhausted, google_exceptions.TooManyRequests):
            if attempt < MAX_RETRIES:
                wait = RETRY_DELAY * attempt
                print(f"[extractor] 429 - retry {attempt}/{MAX_RETRIES} in {wait}s")
                time.sleep(wait)
                continue
            print("[extractor] 429 - max retries hit, skipping batch")
            return []
        except (
            google_exceptions.DeadlineExceeded,
            google_exceptions.InternalServerError,
            google_exceptions.ServiceUnavailable,
        ) as e:
            if attempt < MAX_RETRIES:
                wait = RETRY_DELAY * attempt
                print(f"[extractor] Gemini transient error - retry {attempt}/{MAX_RETRIES} in {wait}s: {e}")
                time.sleep(wait)
                continue
            print(f"[extractor] Gemini transient error - max retries hit: {e}")
            return []
        except google_exceptions.GoogleAPIError as e:
            print(f"[extractor] Gemini API error: {e}")
            return []
        except (KeyError, IndexError, ValueError) as e:
            print(f"[extractor] Parse error: {e}")
            return []
    else:
        return []

    jobs: list[dict] = []
    for item in jobs_payload:
        try:
            source_index = int(item.get("source_index"))
        except (TypeError, ValueError):
            continue

        if source_index < 0 or source_index >= len(posts):
            continue
        if not _is_relevant_tech_job(item):
            continue

        jobs.append(_to_sheet_job(item, posts[source_index]))

    print(f"[extractor] Batch extracted {len(jobs)} jobs from {len(posts)} posts")
    return jobs


def extract_job_data(post: dict) -> dict | None:
    jobs = extract_jobs_data([post])
    return jobs[0] if jobs else None
