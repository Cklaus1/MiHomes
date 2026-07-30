"""M28 · Windows PID liveness must compare the PID field exactly.

`tasklist /FI "PID eq N"` is only a *hint* — Windows can return the header, an
informational "no tasks" line, or (in some locales) partial matches. The old
check did `str(pid) in stdout`, so PID 123 matched a row for PID 1234, or even
matched 123 appearing inside an image name / memory column. The fix parses the
CSV and compares the second column (PID) as an exact integer.
"""

from mihomes.services.gateways.pid import tasklist_has_pid


# A realistic `tasklist /FO CSV` payload (header + one row).
def _csv(pid: int) -> str:
    return (
        '"Image Name","PID","Session Name","Session#","Mem Usage"\n'
        f'"python.exe","{pid}","Console","1","42,000 K"\n'
    )


def test_exact_pid_matches():
    assert tasklist_has_pid(_csv(123), 123) is True


def test_substring_pid_does_not_match():
    # stdout mentions 1234; we asked for 123 — must NOT match (the old bug).
    assert tasklist_has_pid(_csv(1234), 123) is False


def test_pid_in_memory_column_does_not_match():
    # 42,000 K memory column contains "42000"-ish digits; asking for that must fail.
    assert tasklist_has_pid(_csv(999), 42) is False


def test_no_tasks_line_returns_false():
    out = "INFO: No tasks are running which match the specified criteria.\n"
    assert tasklist_has_pid(out, 123) is False


def test_empty_output_returns_false():
    assert tasklist_has_pid("", 123) is False


def test_multiple_rows_finds_exact():
    out = (
        '"Image Name","PID","Session Name","Session#","Mem Usage"\n'
        '"a.exe","1234","Console","1","1 K"\n'
        '"b.exe","55","Console","1","1 K"\n'
    )
    assert tasklist_has_pid(out, 55) is True
    assert tasklist_has_pid(out, 5) is False
