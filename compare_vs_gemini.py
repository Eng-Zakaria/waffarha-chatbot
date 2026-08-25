import csv
import json
import os
import time
from pathlib import Path

from google import genai
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

from rag_engine import RagEngine


# ============================================================
# CONFIG
# ============================================================

INPUT_JSON = "queries.json"
OUTPUT_CSV = "eval/rag_vs_gemini.csv"

GEMINI_MODEL = "gemini-2.5-flash"

# Your local model
LOCAL_MODEL = "qwen2.5:3b-instruct"

# Embedding model used ONLY for comparing answers
EVAL_EMBEDDING_MODEL = "intfloat/multilingual-e5-base"




# ============================================================
# GEMINI
# ============================================================

if not os.getenv("GEMINI_API_KEY"):
    raise RuntimeError(
        "GEMINI_API_KEY environment variable is not set."
    )

gemini_client = genai.Client(
    api_key=os.environ["GEMINI_API_KEY"]
)

# Rate limiter for Gemini API (5 RPM free tier)
class RateLimiter:
    def __init__(self, max_requests_per_minute: int = 5):
        self.max_requests = max_requests_per_minute
        self.requests = []
        self.min_interval = 60.0 / max_requests_per_minute

    def wait_if_needed(self):
        """Wait if we've hit the rate limit."""
        now = time.time()
        # Remove requests older than 1 minute
        self.requests = [t for t in self.requests if now - t < 60]

        # Proactive check: if we're at max_requests - 1, wait to avoid hitting limit
        if len(self.requests) >= self.max_requests:
            # Wait until the oldest request is more than 1 minute old
            oldest = self.requests[0]
            wait_time = 60 - (now - oldest) + 1.0  # Add 1s buffer
            if wait_time > 0:
                print(f"  Gemini rate limit reached. Waiting {wait_time:.1f}s...")
                time.sleep(wait_time)

        # Additional safety: ensure minimum interval between requests
        if self.requests:
            time_since_last = now - self.requests[-1]
            if time_since_last < self.min_interval:
                wait_time = self.min_interval - time_since_last + 0.5
                print(f"  Enforcing minimum interval. Waiting {wait_time:.1f}s...")
                time.sleep(wait_time)

        self.requests.append(time.time())


# Global rate limiter instance
rate_limiter = RateLimiter(max_requests_per_minute=5)


# ============================================================
# ANSWER SIMILARITY MODEL
# ============================================================

print("Loading evaluation embedding model...")

eval_embedding_model = SentenceTransformer(
    EVAL_EMBEDDING_MODEL
)


# ============================================================
# GEMINI PROMPT
# ============================================================

GEMINI_SYSTEM_PROMPT = """
You are the Waffarha customer support assistant.

Rules:
- Answer ONLY using the CONTEXT provided below.
- Do not use outside knowledge.
- Do not invent prices, discounts, offers, policies, or procedures.
- If the context does not contain the answer, clearly say that the information
  is not available.
- Answer in the same language as the user's question.
- Keep the answer short, direct, and practical.
"""


def ask_gemini(question: str, context: str):
    """
    Sends the SAME retrieved context used by the local RAG system
    to Gemini.
    """

    prompt = f"""
{GEMINI_SYSTEM_PROMPT}

CONTEXT:
{context}

USER QUESTION:
{question}
"""

    # Apply rate limiting before making the request
    rate_limiter.wait_if_needed()

    start = time.perf_counter()
    max_retries = 3
    base_wait = 2.0

    for attempt in range(max_retries):
        try:
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
            )

            elapsed = time.perf_counter() - start
            answer = response.text or ""

            return answer.strip(), elapsed
        except Exception as e:
            elapsed = time.perf_counter() - start
            error_str = str(e)

            # Check for quota/rate limit errors (429)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str or "quota" in error_str.lower():
                if attempt < max_retries - 1:
                    # Try to extract retry delay from error message (e.g., "Please retry in 36.17668707s")
                    import re
                    retry_match = re.search(r'Please retry in ([\d.]+)s', error_str)
                    if retry_match:
                        wait_time = float(retry_match.group(1)) + 1.0  # Add 1s buffer
                    else:
                        # Exponential backoff with jitter as fallback
                        wait_time = base_wait * (2 ** attempt) + (time.time() % 1)

                    print(f"  Gemini rate limit hit (attempt {attempt + 1}/{max_retries}). Waiting {wait_time:.1f}s before retry...")
                    time.sleep(wait_time)
                    # Reset rate limiter to allow immediate retry after backoff
                    rate_limiter.requests = []
                    start = time.perf_counter()  # Reset timer
                    continue

            # Non-retryable error or max retries reached
            print(f"  Gemini error: {type(e).__name__}: {e}")
            return "", elapsed

    # Should not reach here, but just in case
    print(f"  Gemini max retries exceeded")
    return "", time.perf_counter() - start


# ============================================================
# LOCAL RAG PROJECT
# ============================================================

print("Loading your RAG engine...")

rag = RagEngine(
    llm_model=LOCAL_MODEL
)


# ============================================================
# SIMILARITY
# ============================================================

def calculate_similarity(expected: str, answer: str) -> float:

    embeddings = eval_embedding_model.encode(
        [expected, answer],
        normalize_embeddings=True
    )

    similarity = cosine_similarity(
        [embeddings[0]],
        [embeddings[1]]
    )[0][0]

    return float(similarity)


def similarity_level(score: float) -> str:

    if score >= 0.90:
        return "Excellent"

    if score >= 0.80:
        return "Good"

    if score >= 0.70:
        return "Fair"

    return "Poor"


# ============================================================
# RUN YOUR PROJECT
# ============================================================

def run_local_project(question: str):

    start = time.perf_counter()

    result = rag.answer(question)

    elapsed = time.perf_counter() - start

    answer = result.get("answer", "")

    # IMPORTANT:
    # RagEngine stores the retrieved documents here.
    retrieved = result.get("sources", [])

    context = rag.build_context(retrieved)

    return answer, context, elapsed


# ============================================================
# MAIN BENCHMARK
# ============================================================

def main():

    input_path = Path(INPUT_JSON)
    output_path = Path(OUTPUT_CSV)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    if not input_path.exists():
        raise FileNotFoundError(
            f"Input JSON not found: {input_path}"
        )

    with open(
        input_path,
        "r",
        encoding="utf-8-sig"
    ) as f:

        rows = json.load(f)

    if not isinstance(rows, list):
        raise ValueError(
            f"Expected {input_path} to contain a JSON array of "
            f"query objects, got {type(rows).__name__}."
        )

    print(f"Loaded {len(rows)} questions.")

    results = []

    for i, row in enumerate(rows, start=1):

        query_id = row.get("id", "")
        category = row.get("category", "")

        question = row.get("query", "").strip()
        expected = row.get("expected_answer", "").strip()

        if not question:
            print(f"[{i}/{len(rows)}] Skipping empty question.")
            continue

        print()
        print("=" * 70)
        print(f"[{i}/{len(rows)}] {question}")

        try:

            # ------------------------------------------------
            # YOUR PROJECT
            # ------------------------------------------------

            project_answer, context, project_time = run_local_project(
                question
            )

            print(
                f"Your RAG: {project_time:.3f}s"
            )

            # ------------------------------------------------
            # GEMINI USING SAME CONTEXT
            # ------------------------------------------------

            if context.strip():

                gemini_answer, gemini_time = ask_gemini(
                    question,
                    context
                )

                # Check if Gemini returned empty answer (likely due to rate limiting)
                if not gemini_answer.strip():
                    print(
                        "WARNING: Gemini returned empty answer. "
                        "This may be due to rate limiting."
                    )

            else:

                gemini_answer = ""
                gemini_time = 0.0

                print(
                    "WARNING: No retrieved context. "
                    "Gemini was not called."
                )

            # ------------------------------------------------
            # SIMILARITY
            # ------------------------------------------------

            if expected:

                project_similarity = calculate_similarity(
                    expected,
                    project_answer
                )

                gemini_similarity = calculate_similarity(
                    expected,
                    gemini_answer
                )

            else:

                project_similarity = ""
                gemini_similarity = ""

            # ------------------------------------------------
            # SAVE RESULT
            # ------------------------------------------------

            results.append({

                "id": query_id,

                "category": category,

                "question": question,

                "expected_answer": expected,

                "project_answer": project_answer,

                "gemini_answer": gemini_answer,

                "project_response_time_sec":
                    round(project_time, 4),

                "gemini_response_time_sec":
                    round(gemini_time, 4),

                "project_accuracy_similarity":
                    round(project_similarity, 4)
                    if project_similarity != ""
                    else "",

                "gemini_accuracy_similarity":
                    round(gemini_similarity, 4)
                    if gemini_similarity != ""
                    else "",

                "project_accuracy_level":
                    similarity_level(project_similarity)
                    if project_similarity != ""
                    else "",

                "gemini_accuracy_level":
                    similarity_level(gemini_similarity)
                    if gemini_similarity != ""
                    else "",

                "context_available":
                    "yes" if context.strip() else "no",

                "status": "success",

            })

            print(
                f"Gemini: {gemini_time:.3f}s"
            )

            if expected:
                print(
                    f"Similarity - Project: "
                    f"{project_similarity:.4f}"
                )

                print(
                    f"Similarity - Gemini: "
                    f"{gemini_similarity:.4f}"
                )

        except Exception as e:

            print(
                f"ERROR: {type(e).__name__}: {e}"
            )

            results.append({

                "id": query_id,

                "category": category,

                "question": question,

                "expected_answer": expected,

                "project_answer": "",

                "gemini_answer": "",

                "project_response_time_sec": "",

                "gemini_response_time_sec": "",

                "project_accuracy_similarity": "",

                "gemini_accuracy_similarity": "",

                "project_accuracy_level": "",

                "gemini_accuracy_level": "",

                "context_available": "",

                "status": f"error: {e}",

            })

    # ========================================================
    # WRITE CSV
    # ========================================================

    fieldnames = [
        "id",
        "category",
        "question",
        "expected_answer",
        "project_answer",
        "gemini_answer",
        "project_response_time_sec",
        "gemini_response_time_sec",
        "project_accuracy_similarity",
        "gemini_accuracy_similarity",
        "project_accuracy_level",
        "gemini_accuracy_level",
        "context_available",
        "status",
    ]

    with open(
        output_path,
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames
        )

        writer.writeheader()
        writer.writerows(results)

    print()
    print("=" * 70)
    print("BENCHMARK COMPLETE")
    print("=" * 70)
    print(f"Results saved to: {output_path}")


if __name__ == "__main__":
    main()