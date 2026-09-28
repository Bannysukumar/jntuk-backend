"""Scrape published JNTUK results for one hall ticket."""

from __future__ import annotations

import aiohttp

from config.settings import JNTUK_RESULTS_API_BASE
from scrapers.jntukClient import JntukRateLimitedError, fetch_notifications, fetch_student_results
from config.collegeDetails import get_college_name
from utils.jntuk import (
    college_code_from_roll,
    determine_degree,
    infer_semester_from_subject_code,
    is_rcrv_title,
    map_year_semester,
    notification_matches_student,
)
from utils.logger import scraping_logger


def _subject_from_row(row: dict) -> dict:
    credits = str(row.get("credits") or row.get("credit") or "0")
    return {
        "subjectCode": str(row.get("subcode") or row.get("subjectCode") or "").strip(),
        "subjectName": str(row.get("subname") or row.get("subjectName") or "").strip(),
        "subjectGrade": str(row.get("grade") or row.get("subjectGrade") or "").strip(),
        "subjectCredits": credits,
        "subjectInternal": str(row.get("internals") or row.get("imf") or "0"),
        "subjectExternal": str(row.get("externals") or row.get("hmfm") or "0"),
        "subjectTotal": str(row.get("total") or row.get("subjectTotal") or "0"),
    }


def _student_name(rows: list[dict], roll_number: str) -> str:
    for row in rows:
        for key in ("stname", "sname", "student_name", "name", "studentname"):
            value = str(row.get(key) or "").strip()
            if value:
                return value
    return roll_number


class ResultScraper:
    def __init__(
        self,
        roll_number,
        omit_exam_codes,
        omit_rcrv_exam_codes,
        url=JNTUK_RESULTS_API_BASE,
    ):
        self.url = url or JNTUK_RESULTS_API_BASE
        self.roll_number = roll_number.upper()
        self.omit_exam_codes = set(omit_exam_codes or [])
        self.omit_rcrv_exam_codes = set(omit_rcrv_exam_codes or [])
        self.results = {"details": {}, "results": []}
        self.logger = scraping_logger

    async def run(self):
        degree = determine_degree(self.roll_number)
        if degree is None:
            self.logger.warning(f"Unsupported JNTUK roll number: {self.roll_number}")
            return None

        try:
            notifications = await fetch_notifications(url=self.url)
        except Exception as error:
            self.logger.error(
                f"Failed to load JNTUK notifications for {self.roll_number}: {error}"
            )
            return None

        relevant = [
            item
            for item in notifications
            if notification_matches_student(item, self.roll_number)
        ]
        if not relevant:
            self.logger.info(f"No published JNTUK exams matched {self.roll_number}")
            return None

        exam_results = []
        async with aiohttp.ClientSession() as session:
            for notification in relevant:
                result_id = str(notification.get("uuid") or "").strip()
                if not result_id:
                    continue
                title = str(notification.get("exam_details") or notification.get("title") or "")
                rcrv = is_rcrv_title(title)
                omit_set = self.omit_rcrv_exam_codes if rcrv else self.omit_exam_codes
                if result_id in omit_set:
                    continue

                try:
                    payload = await fetch_student_results(
                        self.roll_number,
                        result_id,
                        url=self.url,
                        session=session,
                    )
                except JntukRateLimitedError as error:
                    self.logger.warning(
                        f"JNTUK lookup failed for {self.roll_number} {result_id}: {error}"
                    )
                    break
                except Exception as error:
                    self.logger.warning(
                        f"JNTUK lookup failed for {self.roll_number} {result_id}: {error}"
                    )
                    continue

                rows = payload.get("data") if payload.get("status") == 200 else None
                if not isinstance(rows, list) or not rows:
                    continue

                subjects = [
                    subject
                    for subject in (_subject_from_row(row) for row in rows)
                    if subject["subjectCode"]
                ]
                if not subjects:
                    continue

                semester = map_year_semester(notification.get("year_semistore"))
                if semester is None:
                    semester = infer_semester_from_subject_code(subjects[0]["subjectCode"])
                if semester is None:
                    semester = "other"

                if not self.results["details"]:
                    self.results["details"] = {
                        "name": _student_name(rows, self.roll_number),
                        "rollNo": self.roll_number,
                        "collegeCode": college_code_from_roll(self.roll_number),
                        "collegeName": get_college_name(self.roll_number),
                        "fatherName": str(rows[0].get("fname") or rows[0].get("fatherName") or ""),
                    }

                exam_results.append(
                    {
                        "examCode": result_id,
                        "semesterCode": semester,
                        "rcrv": rcrv,
                        "subjects": subjects,
                    }
                )

        if not exam_results:
            return None

        self.results["results"] = exam_results
        return self.results
