"""JNTUK roll-number, regulation, and notification helpers."""

from __future__ import annotations

import re

DEGREE_BY_CODE = {
    "A": "btech",
    "R": "bpharmacy",
    "E": "mba",
    "D": "mtech",
    "S": "mpharmacy",
}

_BTECH_COURSE_MARKERS = (
    "BTECH",
    "B.TECH",
    "B.TECH.",
    "B TECH",
)
_BPHARM_COURSE_MARKERS = (
    "BPHARM",
    "B.PHARM",
    "B PHARM",
    "BPHARMACY",
    "B.PHARMACY",
    "BPHARMCY",
    "BPHARMAY",
    "B.PHARAMCY",
)
_YEAR_SEM_ALIASES = {
    "1-1": "1-1",
    "1-2": "1-2",
    "2-1": "2-1",
    "2-2": "2-2",
    "3-1": "3-1",
    "3-2": "3-2",
    "4-1": "4-1",
    "4-2": "4-2",
    "I-I": "1-1",
    "I-II": "1-2",
    "II-I": "2-1",
    "II-II": "2-2",
    "III-I": "3-1",
    "III-II": "3-2",
    "IV-I": "4-1",
    "IV-II": "4-2",
    "I-1": "1-1",
    "I-2": "1-2",
    "II-1": "2-1",
    "II-2": "2-2",
    "III-1": "3-1",
    "III-2": "3-2",
    "IV-1": "4-1",
    "IV-2": "4-2",
    "I SEMESTER": "1-1",
    "II SEMESTER": "1-2",
    "III SEMESTER": "2-1",
    "IV SEMESTER": "2-2",
}

_SUBJECT_SEMESTER = re.compile(r"^R\d{2}(\d)(\d)", re.IGNORECASE)
_RCRV_MARKERS = (
    "RCRV",
    "RC/RV",
    "RECOUNT",
    "REVALUATION",
    "CHALLENGE REVALUATION",
    "AFTER CH/RC/RV",
    "AFTER CH/RC",
)


def determine_degree(roll_number: str) -> str | None:
    if len(roll_number) < 6:
        return None
    return DEGREE_BY_CODE.get(roll_number[5].upper())


def is_lateral_entry(roll_number: str) -> bool:
    return len(roll_number) >= 5 and roll_number[4] == "5"


def determine_regulation(roll_number: str) -> str:
    """Return the JNTUK academic regulation for a hall ticket."""
    grad_year = int(roll_number[:2])
    effective_year = grad_year - 1 if is_lateral_entry(roll_number) else grad_year
    if effective_year >= 23:
        return "R23"
    if effective_year >= 20:
        return "R20"
    if effective_year >= 19:
        return "R19"
    return "R16"


def college_code_from_roll(roll_number: str) -> str:
    return roll_number[2:4].upper() if len(roll_number) >= 4 else ""


def normalize_course(course: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (course or "").upper())


def course_matches_degree(course: str, degree: str) -> bool:
    normalized = normalize_course(course)
    if degree == "btech":
        return any(normalize_course(marker) in normalized for marker in _BTECH_COURSE_MARKERS)
    if degree == "bpharmacy":
        return any(
            normalize_course(marker) in normalized for marker in _BPHARM_COURSE_MARKERS
        )
    return degree.replace(".", "").upper() in normalized


def map_year_semester(value: str | None) -> str | None:
    if not value:
        return None
    compact = re.sub(r"\s+", " ", value).strip().upper().replace("–", "-")
    if compact in _YEAR_SEM_ALIASES:
        return _YEAR_SEM_ALIASES[compact]
    collapsed = compact.replace(" ", "")
    if collapsed in _YEAR_SEM_ALIASES:
        return _YEAR_SEM_ALIASES[collapsed]
    return None


def infer_semester_from_subject_code(subject_code: str) -> str | None:
    match = _SUBJECT_SEMESTER.match(subject_code or "")
    if not match:
        return None
    year, semester = match.group(1), match.group(2)
    if year in {"1", "2", "3", "4"} and semester in {"1", "2"}:
        return f"{year}-{semester}"
    return None


def is_rcrv_title(title: str) -> bool:
    haystack = (title or "").upper()
    return any(marker in haystack for marker in _RCRV_MARKERS)


def notification_matches_student(notification: dict, roll_number: str) -> bool:
    degree = determine_degree(roll_number)
    if degree is None:
        return False
    course = str(notification.get("course") or "")
    if not course_matches_degree(course, degree):
        return False
    regulation = determine_regulation(roll_number)
    title = " ".join(
        str(notification.get(key) or "")
        for key in ("exam_details", "title")
    ).upper()
    if "ALL YEAR" in title or "ALL SEM" in title:
        return True
    mentioned = [marker for marker in ("R16", "R19", "R20", "R23") if marker in title]
    if not mentioned:
        return True
    return regulation in mentioned


def result_page_url(result_id: str, portal: str) -> str:
    return f"{portal.rstrip('/')}/results?resultId={result_id}"
