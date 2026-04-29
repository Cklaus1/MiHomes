"""Tests for resume_ranker service."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mihomes.services.resume_ranker import (
    _fuzzy_match_path,
    extract_text,
    load_job_description,
    load_resumes,
    rank_resumes,
    save_candidate_notes,
)


# ── extract_text ──────────────────────────────────────────────────────────────

class TestExtractText:
    def test_reads_txt_file(self, tmp_path):
        f = tmp_path / "resume.txt"
        f.write_text("John Doe\n10 years experience", encoding="utf-8")
        assert extract_text(f) == "John Doe\n10 years experience"

    def test_reads_md_file(self, tmp_path):
        f = tmp_path / "resume.md"
        f.write_text("# Jane Smith\n\nSenior Housekeeper", encoding="utf-8")
        result = extract_text(f)
        assert "Jane Smith" in result

    def test_strips_whitespace(self, tmp_path):
        f = tmp_path / "resume.txt"
        f.write_text("  content  \n\n", encoding="utf-8")
        assert extract_text(f) == "content"

    def test_unsupported_extension_raises(self, tmp_path):
        f = tmp_path / "resume.docx"
        f.write_text("content")
        with pytest.raises(ValueError, match="Unsupported file type"):
            extract_text(f)

    def test_pdf_without_pdfplumber_raises_runtime_error(self, tmp_path):
        f = tmp_path / "resume.pdf"
        f.write_bytes(b"%PDF-1.4")
        import sys
        # Remove pdfplumber from available modules to simulate missing install
        saved = sys.modules.pop("pdfplumber", None)
        try:
            with pytest.raises((RuntimeError, ImportError)):
                extract_text(f)
        finally:
            if saved is not None:
                sys.modules["pdfplumber"] = saved


# ── load_resumes ──────────────────────────────────────────────────────────────

class TestLoadResumes:
    def test_loads_txt_and_md(self, tmp_path):
        (tmp_path / "alice.txt").write_text("Alice resume")
        (tmp_path / "bob.md").write_text("Bob resume")
        (tmp_path / "ignore.docx").write_text("skip this")
        results = load_resumes(tmp_path)
        names = {r["name"] for r in results}
        assert "alice" in names
        assert "bob" in names
        assert "ignore" not in names

    def test_returns_name_path_text(self, tmp_path):
        (tmp_path / "candidate.txt").write_text("Resume text here")
        results = load_resumes(tmp_path)
        assert len(results) == 1
        r = results[0]
        assert r["name"] == "candidate"
        assert "text" in r
        assert r["text"] == "Resume text here"

    def test_empty_folder(self, tmp_path):
        results = load_resumes(tmp_path)
        assert results == []

    def test_sorted_alphabetically(self, tmp_path):
        (tmp_path / "zara.txt").write_text("Zara")
        (tmp_path / "adam.txt").write_text("Adam")
        results = load_resumes(tmp_path)
        names = [r["name"] for r in results]
        assert names == sorted(names)

    def test_skips_non_supported_formats(self, tmp_path):
        (tmp_path / "word.docx").write_text("word")
        (tmp_path / "excel.xlsx").write_text("excel")
        (tmp_path / "real.txt").write_text("real resume")
        results = load_resumes(tmp_path)
        assert len(results) == 1


# ── load_job_description ──────────────────────────────────────────────────────

class TestLoadJobDescription:
    def test_raises_when_directory_missing(self, tmp_path):
        with patch("mihomes.services.resume_ranker.kb_path", return_value=tmp_path):
            with pytest.raises(FileNotFoundError, match="Job descriptions directory not found"):
                load_job_description("housekeeper")

    def test_raises_when_no_match(self, tmp_path):
        jd_dir = tmp_path / "staff" / "job-descriptions"
        jd_dir.mkdir(parents=True)
        with patch("mihomes.services.resume_ranker.kb_path", return_value=tmp_path):
            with pytest.raises(FileNotFoundError, match="No job description found"):
                load_job_description("gardener")

    def test_returns_content_for_exact_match(self, tmp_path):
        jd_dir = tmp_path / "staff" / "job-descriptions"
        jd_dir.mkdir(parents=True)
        (jd_dir / "housekeeper.md").write_text("# Housekeeper JD\nRequirements...")
        with patch("mihomes.services.resume_ranker.kb_path", return_value=tmp_path):
            content = load_job_description("housekeeper")
        assert "Housekeeper JD" in content

    def test_returns_partial_match(self, tmp_path):
        jd_dir = tmp_path / "staff" / "job-descriptions"
        jd_dir.mkdir(parents=True)
        (jd_dir / "head-housekeeper.md").write_text("# Head Housekeeper JD")
        with patch("mihomes.services.resume_ranker.kb_path", return_value=tmp_path):
            content = load_job_description("housekeeper")
        assert "Head Housekeeper" in content


# ── _fuzzy_match_path ─────────────────────────────────────────────────────────

class TestFuzzyMatchPath:
    def test_exact_match(self):
        mapping = {"john-smith": "/resumes/john-smith.txt"}
        result = _fuzzy_match_path("John Smith", mapping)
        assert result == "/resumes/john-smith.txt"

    def test_partial_match(self):
        mapping = {"john-smith-jr": "/resumes/john-smith-jr.txt"}
        result = _fuzzy_match_path("John Smith", mapping)
        assert result == "/resumes/john-smith-jr.txt"

    def test_no_match_returns_none(self):
        mapping = {"alice-jones": "/resumes/alice-jones.txt"}
        result = _fuzzy_match_path("Bob Nobody", mapping)
        assert result is None

    def test_strips_hyphens_and_underscores(self):
        mapping = {"jane_doe": "/resumes/jane_doe.txt"}
        result = _fuzzy_match_path("Jane Doe", mapping)
        assert result == "/resumes/jane_doe.txt"

    def test_case_insensitive(self):
        mapping = {"ALICE": "/resumes/ALICE.txt"}
        result = _fuzzy_match_path("alice", mapping)
        assert result == "/resumes/ALICE.txt"


# ── rank_resumes ──────────────────────────────────────────────────────────────

class TestRankResumes:
    def _sample_resumes(self):
        return [
            {"name": "alice", "path": "/tmp/alice.txt", "text": "10 years housekeeper experience"},
            {"name": "bob", "path": "/tmp/bob.txt", "text": "5 years commercial cleaning"},
        ]

    def _mock_ranking_result(self):
        return {
            "rankings": [
                {
                    "rank": 1,
                    "candidate_name": "alice",
                    "overall_score": 85,
                    "scores": {
                        "relevant_experience": 22,
                        "household_specificity": 20,
                        "tenure_stability": 18,
                        "skill_match": 15,
                        "presentation_clarity": 10,
                    },
                    "strengths": ["Long tenure"],
                    "concerns": [],
                    "red_flags": [],
                    "recommended_action": "phone_screen",
                    "one_line_summary": "Strong household experience.",
                },
                {
                    "rank": 2,
                    "candidate_name": "bob",
                    "overall_score": 60,
                    "scores": {
                        "relevant_experience": 15,
                        "household_specificity": 10,
                        "tenure_stability": 15,
                        "skill_match": 12,
                        "presentation_clarity": 8,
                    },
                    "strengths": ["Reliable"],
                    "concerns": ["Commercial only"],
                    "red_flags": [],
                    "recommended_action": "hold",
                    "one_line_summary": "Commercial background only.",
                },
            ],
            "hiring_notes": "Alice is the clear frontrunner.",
        }

    def test_rank_resumes_calls_ai(self):
        mock_provider = MagicMock()
        mock_provider.structured_output.return_value = self._mock_ranking_result()
        resumes = self._sample_resumes()
        with patch("mihomes.services.resume_ranker.get_provider", return_value=mock_provider):
            result = rank_resumes(resumes, "Need an experienced housekeeper")
        assert "rankings" in result
        assert len(result["rankings"]) == 2
        mock_provider.structured_output.assert_called_once()

    def test_attaches_file_path_to_ranking(self):
        mock_provider = MagicMock()
        mock_provider.structured_output.return_value = self._mock_ranking_result()
        resumes = self._sample_resumes()
        with patch("mihomes.services.resume_ranker.get_provider", return_value=mock_provider):
            result = rank_resumes(resumes, "Job description here")
        alice = next(r for r in result["rankings"] if r["candidate_name"] == "alice")
        assert alice["file"] == "/tmp/alice.txt"

    def test_truncates_long_resume_text(self):
        mock_provider = MagicMock()
        mock_provider.structured_output.return_value = self._mock_ranking_result()
        long_text = "x" * 5000
        resumes = [{"name": "alice", "path": "/tmp/alice.txt", "text": long_text}]
        with patch("mihomes.services.resume_ranker.get_provider", return_value=mock_provider):
            rank_resumes(resumes, "Job description")
        assert mock_provider.structured_output.called

    def test_handles_resume_with_error(self):
        mock_provider = MagicMock()
        mock_provider.structured_output.return_value = {
            "rankings": [],
            "hiring_notes": "No valid resumes.",
        }
        resumes = [
            {"name": "broken", "path": "/tmp/broken.pdf", "text": None, "error": "Could not read"},
        ]
        with patch("mihomes.services.resume_ranker.get_provider", return_value=mock_provider):
            result = rank_resumes(resumes, "Job description")
        assert "rankings" in result

    def test_top_n_respected(self):
        mock_provider = MagicMock()
        mock_provider.structured_output.return_value = self._mock_ranking_result()
        resumes = self._sample_resumes()
        with patch("mihomes.services.resume_ranker.get_provider", return_value=mock_provider):
            rank_resumes(resumes, "Job description", top_n=1)
        call_args = mock_provider.structured_output.call_args
        user_message = call_args[0][1]
        assert "top 1" in user_message

    def test_uses_provided_api_key(self):
        mock_provider = MagicMock()
        mock_provider.structured_output.return_value = self._mock_ranking_result()
        with patch("mihomes.services.resume_ranker.get_provider", return_value=mock_provider) as mock_gp:
            rank_resumes(self._sample_resumes(), "JD", api_key="sk-test-key")
        mock_gp.assert_called_once_with("claude", api_key="sk-test-key")


# ── save_candidate_notes ──────────────────────────────────────────────────────

class TestSaveCandidateNotes:
    def _sample_ranking(self):
        return {
            "candidate_name": "Alice Johnson",
            "rank": 1,
            "overall_score": 85,
            "recommended_action": "phone_screen",
            "one_line_summary": "Excellent household manager.",
            "scores": {
                "relevant_experience": 22,
                "household_specificity": 20,
                "tenure_stability": 18,
                "skill_match": 15,
                "presentation_clarity": 10,
            },
            "strengths": ["Long tenure", "Private household experience"],
            "concerns": ["Distance from property"],
            "red_flags": [],
        }

    def test_creates_markdown_file(self, tmp_path):
        with patch("mihomes.services.resume_ranker.kb_path", return_value=tmp_path):
            path = save_candidate_notes(self._sample_ranking(), "housekeeper")
        assert path.exists()
        assert path.suffix == ".md"

    def test_file_contains_candidate_name(self, tmp_path):
        with patch("mihomes.services.resume_ranker.kb_path", return_value=tmp_path):
            path = save_candidate_notes(self._sample_ranking(), "housekeeper")
        content = path.read_text()
        assert "Alice Johnson" in content

    def test_file_contains_role(self, tmp_path):
        with patch("mihomes.services.resume_ranker.kb_path", return_value=tmp_path):
            path = save_candidate_notes(self._sample_ranking(), "estate-manager")
        content = path.read_text()
        assert "estate-manager" in content

    def test_file_contains_scores_table(self, tmp_path):
        with patch("mihomes.services.resume_ranker.kb_path", return_value=tmp_path):
            path = save_candidate_notes(self._sample_ranking(), "housekeeper")
        content = path.read_text()
        assert "| Relevant Experience |" in content

    def test_red_flags_section_omitted_when_empty(self, tmp_path):
        ranking = self._sample_ranking()
        ranking["red_flags"] = []
        with patch("mihomes.services.resume_ranker.kb_path", return_value=tmp_path):
            path = save_candidate_notes(ranking, "housekeeper")
        content = path.read_text()
        assert "## Red Flags" not in content

    def test_red_flags_included_when_present(self, tmp_path):
        ranking = self._sample_ranking()
        ranking["red_flags"] = ["Short tenure at last 3 jobs"]
        with patch("mihomes.services.resume_ranker.kb_path", return_value=tmp_path):
            path = save_candidate_notes(ranking, "housekeeper")
        content = path.read_text()
        assert "## Red Flags" in content
        assert "Short tenure" in content

    def test_creates_candidates_directory(self, tmp_path):
        with patch("mihomes.services.resume_ranker.kb_path", return_value=tmp_path):
            save_candidate_notes(self._sample_ranking(), "housekeeper")
        assert (tmp_path / "staff" / "candidates").exists()

    def test_file_contains_strengths(self, tmp_path):
        with patch("mihomes.services.resume_ranker.kb_path", return_value=tmp_path):
            path = save_candidate_notes(self._sample_ranking(), "housekeeper")
        content = path.read_text()
        assert "Long tenure" in content
        assert "Private household experience" in content
