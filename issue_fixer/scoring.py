"""Confidence scoring system for automated fixes.

Each fix gets a composite score (0-100) based on multiple signals:
- Patch application: did all patches apply cleanly?
- Review quality: how did the Review Agent score it?
- Sandbox verification: did the fixed code pass syntax checks?
- Dependency risk: how many files are affected?

Score interpretation:
  90-100: High confidence — safe to auto-merge
  70-89:  Medium confidence — review recommended
  50-69:  Low confidence — manual review required
  0-49:   Very low confidence — likely needs rework
"""

from dataclasses import dataclass


@dataclass
class ConfidenceScore:
    """Composite confidence score with breakdown."""
    total: int  # 0-100
    patch_score: int  # 0-100
    review_score: int  # 0-100
    sandbox_score: int  # 0-100
    dependency_score: int  # 0-100
    label: str  # "high", "medium", "low", "very_low"
    needs_review: bool
    notes: list[str]


def compute_confidence(
    files_to_fix: list[dict],
    review_score: int = 0,
    sandbox_passed: int = 0,
    sandbox_total: int = 0,
    affected_files: int = 0,
) -> ConfidenceScore:
    """Compute composite confidence score for a fix.

    Args:
        files_to_fix: List of file fix dicts (with patch_stats)
        review_score: Score from the Review Agent (0-100)
        sandbox_passed: Number of files that passed sandbox verification
        sandbox_total: Total files checked in sandbox
        affected_files: Number of cross-file dependents

    Returns:
        ConfidenceScore with breakdown and label
    """
    notes = []

    # 1. Patch application score (weight: 30%)
    if files_to_fix:
        total_patches = 0
        applied_patches = 0
        for f in files_to_fix:
            ps = f.get("patch_stats", {})
            total_patches += ps.get("applied", 0) + ps.get("failed", 0)
            applied_patches += ps.get("applied", 0)
        if total_patches > 0:
            patch_ratio = applied_patches / total_patches
            patch_score = int(patch_ratio * 100)
            if patch_ratio < 1.0:
                notes.append(f"{total_patches - applied_patches} patch(es) failed to apply")
        else:
            # Full file mode, no patches — assume OK
            patch_score = 80
    else:
        patch_score = 0
        notes.append("No files to fix")

    # 2. Review score (weight: 35%)
    review_adj = review_score
    if review_score == 0:
        review_adj = 50  # no review, neutral
        notes.append("No review performed")

    # 3. Sandbox verification score (weight: 20%)
    if sandbox_total > 0:
        sandbox_ratio = sandbox_passed / sandbox_total
        sandbox_score = int(sandbox_ratio * 100)
        if sandbox_ratio < 1.0:
            notes.append(f"{sandbox_total - sandbox_passed} file(s) failed syntax check")
    else:
        sandbox_score = 70  # no sandbox, neutral
        notes.append("No sandbox verification")

    # 4. Dependency risk score (weight: 15%)
    # More affected files = higher risk = lower score
    if affected_files == 0:
        dependency_score = 100
    elif affected_files <= 2:
        dependency_score = 80
    elif affected_files <= 5:
        dependency_score = 60
        notes.append(f"{affected_files} dependent files may need updates")
    else:
        dependency_score = 40
        notes.append(f"High dependency risk: {affected_files} affected files")

    # Composite score
    total = int(
        patch_score * 0.30
        + review_adj * 0.35
        + sandbox_score * 0.20
        + dependency_score * 0.15
    )
    total = max(0, min(100, total))

    # Label
    if total >= 90:
        label = "high"
        needs_review = False
    elif total >= 70:
        label = "medium"
        needs_review = False
    elif total >= 50:
        label = "low"
        needs_review = True
        notes.append("Manual review recommended")
    else:
        label = "very_low"
        needs_review = True
        notes.append("Manual review required — fix likely needs rework")

    return ConfidenceScore(
        total=total,
        patch_score=patch_score,
        review_score=review_adj,
        sandbox_score=sandbox_score,
        dependency_score=dependency_score,
        label=label,
        needs_review=needs_review,
        notes=notes,
    )


def format_confidence(score: ConfidenceScore) -> str:
    """Format confidence score as human-readable text."""
    label_colors = {
        "high": "green",
        "medium": "yellow",
        "low": "red",
        "very_low": "bold red",
    }
    color = label_colors.get(score.label, "white")

    lines = [
        f"Confidence: {score.total}/100 [{color}]{score.label}[/{color}]",
        f"  Patch: {score.patch_score}/100  Review: {score.review_score}/100  "
        f"Sandbox: {score.sandbox_score}/100  Deps: {score.dependency_score}/100",
    ]
    if score.needs_review:
        lines.append(f"  [!] Manual review recommended")
    for note in score.notes:
        lines.append(f"  - {note}")

    return "\n".join(lines)
