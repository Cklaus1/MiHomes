"""Resume ranker — AI force-ranks candidates from a folder of resumes.

Supported formats: .pdf, .txt, .md
Reads the job description from knowledge/staff/job-descriptions/<role>.md
Returns a ranked list with scores and reasoning for each candidate.
"""

import os
import re
from pathlib import Path
from typing import Optional

from mihomes.services.playbook import kb_path
from mihomes.services.ai.provider import get_provider, AIProviderError


# ── File ingestion ────────────────────────────────────────────────────────────

def extract_text(path: Path) -> str:
    """Extract plain text from a resume file (.pdf, .txt, .md)."""
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                pages = [page.extract_text() or "" for page in pdf.pages]
            text = "\n".join(pages).strip()
            if not text:
                raise ValueError("PDF appears to be image-only (no extractable text)")
            return text
        except ImportError:
            raise RuntimeError("pdfplumber not installed. Run: pip install pdfplumber")

    elif suffix in (".txt", ".md"):
        return path.read_text(errors="ignore").strip()

    else:
        raise ValueError(f"Unsupported file type: {suffix}. Use .pdf, .txt, or .md")


def load_resumes(folder: Path) -> list[dict]:
    """Load all resumes from a folder. Returns list of {name, path, text}."""
    supported = {".pdf", ".txt", ".md"}
    resumes = []
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() not in supported:
            continue
        try:
            text = extract_text(f)
            resumes.append({"name": f.stem, "path": str(f), "text": text})
        except Exception as e:
            resumes.append({"name": f.stem, "path": str(f), "text": None, "error": str(e)})
    return resumes


def load_job_description(role: str) -> str:
    """Load a job description from knowledge/staff/job-descriptions/<role>.md"""
    jd_dir = kb_path() / "staff" / "job-descriptions"
    if not jd_dir.exists():
        raise FileNotFoundError(
            f"Job descriptions directory not found: {jd_dir}\n"
            f"Create it and add a file: knowledge/staff/job-descriptions/{role}.md"
        )
    # Try exact match first, then partial
    candidates = list(jd_dir.glob(f"{role}*.md")) + list(jd_dir.glob(f"*{role}*.md"))
    if not candidates:
        raise FileNotFoundError(
            f"No job description found for '{role}' in {jd_dir}/\n"
            f"Create one at: knowledge/staff/job-descriptions/{role}.md"
        )
    return candidates[0].read_text()


# ── AI ranking ────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert hiring consultant for private household and estate management staff.
You evaluate resumes objectively based on the job description provided, with no bias toward name,
age, gender, or background. Your job is to force-rank candidates so the hiring manager can focus
their time on the most promising people."""

RANKING_SCHEMA = {
    "type": "object",
    "properties": {
        "rankings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rank":           {"type": "integer"},
                    "candidate_name": {"type": "string"},
                    "overall_score":  {"type": "integer", "description": "0-100"},
                    "scores": {
                        "type": "object",
                        "properties": {
                            "relevant_experience":   {"type": "integer", "description": "0-25"},
                            "household_specificity": {"type": "integer", "description": "0-25"},
                            "tenure_stability":      {"type": "integer", "description": "0-20"},
                            "skill_match":           {"type": "integer", "description": "0-20"},
                            "presentation_clarity":  {"type": "integer", "description": "0-10"},
                        },
                        "required": ["relevant_experience", "household_specificity", "tenure_stability", "skill_match", "presentation_clarity"],
                    },
                    "strengths":          {"type": "array", "items": {"type": "string"}},
                    "concerns":           {"type": "array", "items": {"type": "string"}},
                    "red_flags":          {"type": "array", "items": {"type": "string"}},
                    "recommended_action": {"type": "string", "enum": ["phone_screen", "hold", "decline"]},
                    "one_line_summary":   {"type": "string"},
                },
                "required": ["rank", "candidate_name", "overall_score", "scores", "strengths", "concerns", "red_flags", "recommended_action", "one_line_summary"],
            },
        },
        "hiring_notes": {"type": "string", "description": "Overall observations about the candidate pool"},
    },
    "required": ["rankings", "hiring_notes"],
}


def rank_resumes(
    resumes: list[dict],
    job_description: str,
    top_n: int = 10,
    api_key: Optional[str] = None,
) -> dict:
    """
    AI force-ranks candidates against the job description.

    Scoring dimensions (100 points total):
    - Relevant experience (25): years and recency of household/hospitality work
    - Household specificity (25): private home vs commercial — private is higher value
    - Tenure stability (20): average tenure per role; short stints are a flag
    - Skill match (20): specific skills listed in JD vs resume
    - Presentation clarity (10): resume is organized, dates clear, no gaps unexplained

    Returns top_n candidates ranked 1–N with scores, strengths, concerns, red flags,
    and a recommended action (phone_screen / hold / decline) for each.
    """
    provider = get_provider("claude", api_key=api_key)

    # Build resume block — truncate very long resumes to ~2000 chars to manage tokens
    resume_blocks = []
    for i, r in enumerate(resumes, 1):
        if r.get("error") or not r.get("text"):
            resume_blocks.append(f"=== CANDIDATE {i}: {r['name']} ===\n[Could not extract text: {r.get('error', 'empty file')}]\n")
        else:
            text = r["text"]
            if len(text) > 2500:
                text = text[:2500] + "\n... [truncated]"
            resume_blocks.append(f"=== CANDIDATE {i}: {r['name']} ===\n{text}\n")

    resumes_text = "\n".join(resume_blocks)

    user_message = f"""Please force-rank the following {len(resumes)} candidates for this role.

Return the top {min(top_n, len(resumes))} candidates only, ranked from best to worst fit.
For any candidate you cannot evaluate (unreadable resume), include them at the bottom with a note.

<job_description>
{job_description}
</job_description>

<resumes>
{resumes_text}
</resumes>

Force-rank all candidates. Do not group or tie — every rank must be unique.
Be direct about red flags. A short tenure pattern, gaps, or no household experience are red flags."""

    result = provider.structured_output(SYSTEM_PROMPT, user_message, RANKING_SCHEMA)

    # Attach file paths back to results
    name_to_path = {r["name"]: r["path"] for r in resumes}
    for ranking in result.get("rankings", []):
        name = ranking.get("candidate_name", "")
        # Try to match candidate name to file name (AI may not use exact filename)
        ranking["file"] = name_to_path.get(name, _fuzzy_match_path(name, name_to_path))

    return result


def _fuzzy_match_path(candidate_name: str, name_to_path: dict) -> Optional[str]:
    """Try to match an AI-returned name to a filename."""
    cn_lower = candidate_name.lower().replace(" ", "").replace("-", "").replace("_", "")
    for fname, path in name_to_path.items():
        fn_lower = fname.lower().replace(" ", "").replace("-", "").replace("_", "")
        if cn_lower in fn_lower or fn_lower in cn_lower:
            return path
    return None


# ── Candidate notes ───────────────────────────────────────────────────────────

def save_candidate_notes(ranking: dict, role: str) -> Path:
    """Save AI ranking notes for a candidate as a markdown file."""
    from datetime import date
    notes_dir = kb_path() / "staff" / "candidates"
    notes_dir.mkdir(parents=True, exist_ok=True)

    candidate_name = ranking.get("candidate_name") or "unknown"
    name = candidate_name.replace(" ", "-").lower()
    filename = notes_dir / f"{name}-{date.today()}.md"

    action = (ranking.get("recommended_action") or "unknown").replace("_", " ").title()
    lines = [
        f"# Candidate: {candidate_name}",
        f"",
        f"**Role**: {role}  ",
        f"**Rank**: #{ranking.get('rank', '?')}  ",
        f"**Overall Score**: {ranking.get('overall_score', '?')}/100  ",
        f"**Recommended Action**: {action}  ",
        f"",
        f"## Summary",
        f"",
        f"{ranking.get('one_line_summary', '')}",
        f"",
        f"## Scores",
        f"",
        f"| Dimension | Score |",
        f"|-----------|-------|",
    ]
    for k, v in ranking.get("scores", {}).items():
        label = k.replace("_", " ").title()
        lines.append(f"| {label} | {v} |")

    lines += ["", "## Strengths", ""]
    for s in ranking.get("strengths", []):
        lines.append(f"- {s}")

    lines += ["", "## Concerns", ""]
    for c in ranking.get("concerns", []):
        lines.append(f"- {c}")

    if ranking.get("red_flags"):
        lines += ["", "## Red Flags", ""]
        for rf in ranking["red_flags"]:
            lines.append(f"- {rf}")

    lines += [
        "",
        "## Phone Screen Notes",
        "",
        "_Fill in after phone screen._",
        "",
        "## Trial Day Notes",
        "",
        "_Fill in after trial day._",
        "",
        "## Reference Check",
        "",
        "_Fill in after reference calls._",
    ]

    filename.write_text("\n".join(lines))
    return filename
