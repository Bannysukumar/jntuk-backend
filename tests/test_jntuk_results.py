import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, status
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for name, value in {
    "RABBITMQ_URL": "amqp://guest:guest@localhost/",
    "DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/jntuk",
    "QUEUE_NAME": "test",
    "REDIS_URL": "redis://localhost:6379/0",
    "VAPID_PUBLIC_KEY": "test",
    "VAPID_PRIVATE_KEY": "test",
    "TELEGRAM_TOKEN": "test",
    "TELEGRAM_CHAT_ID": "test",
    "AWS_ACCESS_KEY_ID": "test",
    "AWS_SECRET_ACCESS_KEY": "test",
    "AWS_REGION": "us-east-1",
    "S3_BUCKET_NAME": "test",
    "GRACE_MARKS_ADMIN_KEY": "test",
}.items():
    os.environ.setdefault(name, value)

from database.models import (  # noqa: E402
    studentAllResultsModel,
    studentBacklogs,
    studentCredits,
    studentDetailsModel,
    studentResultContrast,
    studentResultsModel,
)
from scrapers.resultScraper import ResultScraper, _subject_from_row  # noqa: E402
from utils.helpers import (  # noqa: E402
    getGradeValue,
    get_credit_regulation_details,
    isGreat,
    is_failing_grade,
)
from config.collegeDetails import get_college_name  # noqa: E402
from utils.jntuk import (  # noqa: E402
    determine_regulation,
    infer_semester_from_subject_code,
    map_year_semester,
    notification_matches_student,
)


def _mark(
    semester,
    exam,
    code,
    name,
    grade,
    credits,
    rcrv=False,
    grace=False,
):
    return SimpleNamespace(
        semesterCode=semester,
        examCode=exam,
        rcrv=rcrv,
        graceMarks=grace,
        internalMarks="0",
        externalMarks="0",
        totalMarks="0",
        grades=grade,
        credits=float(credits),
        subject=SimpleNamespace(subjectCode=code, subjectName=name),
    )


def _student(roll="226Q1A4304"):
    return SimpleNamespace(
        name=roll,
        rollNumber=roll,
        collegeCode=roll[2:4],
        fatherName="",
    )


THREE_TWO_MARKS = [
    _mark("3-2", "exam-32", "R2032058", "MEAN STACK SOC", "A+", 2),
    _mark("3-2", "exam-32", "R2032059", "EMPLOYABILITY SKILLS-II", "COMPLETED", 0),
    _mark("3-2", "exam-32", "R2032421", "COMPUTER NETWORKS", "E", 3),
    _mark("3-2", "exam-32", "R203242A", "SOFTWARE PROJECT MANAGEMENT", "C", 3),
]
THREE_ONE_MARKS = [
    _mark("3-1", "exam-31", "R203142A", "SOFTWARE ENGINEERING", "F", 0),
    _mark("3-1", "exam-31-supply", "R203142A", "SOFTWARE ENGINEERING", "D", 3),
    _mark("3-1", "exam-31", "R2031422", "OPERATING SYSTEMS", "E", 3),
]


def test_jntuk_grade_points_and_failing_rules():
    assert getGradeValue("A+", False) == 10
    assert getGradeValue("E", False) == 5
    assert getGradeValue("COMPLETED", False) == 0
    assert is_failing_grade("F") is True
    assert is_failing_grade("Ab") is True
    assert is_failing_grade("COMPLETED") is False
    assert isGreat("F", "D") is True
    assert isGreat("A+", "E") is False


def test_jntuk_regulation_and_semester_mapping():
    assert determine_regulation("226Q1A4304") == "R20"
    assert determine_regulation("236Q1A4304") == "R23"
    assert determine_regulation("236Q5A4304") == "R20"
    assert map_year_semester("III-II") == "3-2"
    assert infer_semester_from_subject_code("R2032421") == "3-2"
    assert notification_matches_student(
        {
            "course": "B.Tech",
            "exam_details": "III B.Tech II Semester (R16R19R20) Reg / Supple",
            "regulations": "BT (R23,R20,R19,R16) - BP (PCI)",
        },
        "226Q1A4304",
    )
    assert not notification_matches_student(
        {
            "course": "B.Tech",
            "exam_details": "I B.Tech II Sem (R23)(BiPC) Reg. Examinations",
            "regulations": "BT (R23,R20,R19,R16) - BP (PCI)",
        },
        "226Q1A4304",
    )


def test_academic_result_keeps_best_grade_and_ignores_completed_backlogs():
    result = studentResultsModel(THREE_ONE_MARKS + THREE_TWO_MARKS, False)
    software = next(
        subject
        for semester in result["semesters"]
        if semester["semester"] == "3-1"
        for subject in semester["subjects"]
        if subject["subjectCode"] == "R203142A"
    )
    assert software["grades"] == "D"
    assert software["credits"] == 3.0

    three_two = next(sem for sem in result["semesters"] if sem["semester"] == "3-2")
    assert three_two["backlogs"] == 0
    assert three_two["semesterSGPA"] == "7.00"


def test_backlogs_only_include_still_failing_subjects():
    failing = studentBacklogs(
        [
            _mark("2-1", "exam-21", "R2021011", "MATHEMATICS-III", "F", 0),
            _mark("3-1", "exam-31", "R203142A", "SOFTWARE ENGINEERING", "D", 3),
        ],
        False,
    )
    assert failing["totalBacklogs"] == 1
    assert failing["semesters"][0]["subjects"][0]["subjectCode"] == "R2021011"


def test_all_results_keep_regular_and_supply_attempts():
    history = studentAllResultsModel(THREE_ONE_MARKS)
    exams = history[0]["exams"]
    assert {exam["examCode"] for exam in exams} == {"exam-31", "exam-31-supply"}


def test_student_details_include_hall_ticket_and_college_name():
    details = studentDetailsModel(
        SimpleNamespace(
            name="226Q1A4304",
            rollNumber="226Q1A4304",
            collegeCode="6Q",
            fatherName="",
        )
    )
    assert details["rollNumber"] == "226Q1A4304"
    assert details["collegeCode"] == "6Q"
    assert details["collegeName"] == (
        "Kakinada Institute of Engineering and Technology - II, Korangi"
    )


def test_college_name_comes_from_hall_ticket():
    assert get_college_name("226Q1A4304") == (
        "Kakinada Institute of Engineering and Technology - II, Korangi"
    )
    assert get_college_name("22JN1A4330") == (
        "Kakinada Institute of Engineering and Technology for Women, Korangi"
    )
    assert get_college_name("22021A0101") == (
        "University College of Engineering, Kakinada"
    )
    assert get_college_name("22XX1A0501") == "Unknown"


def test_credits_checker_uses_jntuk_r20_table():
    credits = get_credit_regulation_details("226Q1A4304")
    assert credits["4"]["Total"] == "160"
    breakdown = studentCredits(THREE_TWO_MARKS, credits, False)
    assert breakdown["totalObtainedCredits"] == 8.0
    assert credits["4"]["Required"] == "160"


def test_result_contrast_aligns_two_students():
    first = {
        "details": {
            "name": "A",
            "rollNumber": "226Q1A4304",
            "collegeCode": "6Q",
            "fatherName": "",
        },
        "results": studentResultsModel(THREE_TWO_MARKS, False),
    }
    second = {
        "details": {
            "name": "B",
            "rollNumber": "226Q1A4305",
            "collegeCode": "6Q",
            "fatherName": "",
        },
        "results": studentResultsModel(THREE_ONE_MARKS, False),
    }
    contrast = studentResultContrast(first, second)
    assert len(contrast["studentProfiles"]) == 2
    assert contrast["studentProfiles"][0]["rollNumber"] == "226Q1A4304"
    assert contrast["studentProfiles"][0]["collegeName"].startswith(
        "Kakinada Institute of Engineering and Technology"
    )
    assert contrast["semesters"]


def test_notification_mapping_builds_jntuk_exam_rows():
    from utils.jntuk import result_page_url

    assert map_year_semester("III-II") == "3-2"
    assert "resultId=abc-123" in result_page_url(
        "abc-123", "https://jntukresults.edu.in"
    )


def test_scraper_maps_jntuk_rows():
    subject = _subject_from_row(
        {
            "subcode": "R2032421",
            "subname": "COMPUTER NETWORKS",
            "credits": "3",
            "grade": "E",
        }
    )
    assert subject["subjectCode"] == "R2032421"
    assert subject["subjectGrade"] == "E"
    assert subject["subjectCredits"] == "3"


def test_scraper_skips_known_exams_and_empty_lookups():
    scraper = ResultScraper(
        "226Q1A4304",
        omit_exam_codes={"known-id"},
        omit_rcrv_exam_codes=set(),
        url="https://jntuk.example/jntukresults",
    )

    async def fake_notifications(url=None, use_cache=True):
        return [
            {
                "uuid": "known-id",
                "course": "BTECH",
                "exam_details": "III B.Tech II Sem (R20) Regular",
                "regulations": "R20",
                "year_semistore": "III-II",
            },
            {
                "uuid": "fresh-id",
                "course": "BTECH",
                "exam_details": "III B.Tech I Sem (R20) Regular",
                "regulations": "R20",
                "year_semistore": "III-I",
            },
        ]

    async def fake_student_results(roll, result_id, url=None, session=None):
        return {
            "status": 200,
            "data": [
                {
                    "htno": roll,
                    "subcode": "R2031422",
                    "subname": "OPERATING SYSTEMS",
                    "credits": "3",
                    "grade": "E",
                }
            ],
        }

    with (
        patch("scrapers.resultScraper.fetch_notifications", new=fake_notifications),
        patch("scrapers.resultScraper.fetch_student_results", new=fake_student_results),
    ):
        result = asyncio.run(scraper.run())

    assert result["details"]["rollNo"] == "226Q1A4304"
    assert result["details"]["collegeCode"] == "6Q"
    assert result["details"]["collegeName"] == (
        "Kakinada Institute of Engineering and Technology - II, Korangi"
    )
    assert result["results"][0]["examCode"] == "fresh-id"
    assert result["results"][0]["semesterCode"] == "3-1"


def test_scraper_keeps_partial_results_after_rate_limit():
    scraper = ResultScraper(
        "226Q1A4304",
        omit_exam_codes=set(),
        omit_rcrv_exam_codes=set(),
        url="https://jntuk.example/jntukresults",
    )

    async def fake_notifications(url=None, use_cache=True):
        return [
            {
                "uuid": "ok-id",
                "course": "BTECH",
                "exam_details": "III B.Tech II Sem (R20) Regular",
                "year_semistore": "III-II",
            },
            {
                "uuid": "rate-id",
                "course": "BTECH",
                "exam_details": "III B.Tech I Sem (R20) Regular",
                "year_semistore": "III-I",
            },
        ]

    async def fake_student_results(roll, result_id, url=None, session=None):
        from scrapers.jntukClient import JntukRateLimitedError

        if result_id == "rate-id":
            raise JntukRateLimitedError("JNTUK rate-limited")
        return {
            "status": 200,
            "data": [
                {
                    "htno": roll,
                    "subcode": "R2032421",
                    "subname": "COMPUTER NETWORKS",
                    "credits": "3",
                    "grade": "E",
                }
            ],
        }

    with (
        patch("scrapers.resultScraper.fetch_notifications", new=fake_notifications),
        patch("scrapers.resultScraper.fetch_student_results", new=fake_student_results),
    ):
        result = asyncio.run(scraper.run())

    assert result is not None
    assert [exam["examCode"] for exam in result["results"]] == ["ok-id"]


def test_scraper_skips_an_initial_rate_limit_and_keeps_later_exams():
    scraper = ResultScraper(
        "226Q1A4304",
        omit_exam_codes=set(),
        omit_rcrv_exam_codes=set(),
        url="https://jntuk.example/jntukresults",
    )

    async def fake_notifications(url=None, use_cache=True):
        return [
            {
                "uuid": "rate-id",
                "course": "BTECH",
                "exam_details": "I B.Tech I Sem (R20) Supplementary",
                "year_semistore": "I-I",
            },
            {
                "uuid": "ok-id",
                "course": "BTECH",
                "exam_details": "III B.Tech II Sem (R20) Regular",
                "year_semistore": "III-II",
            },
        ]

    async def fake_student_results(roll, result_id, url=None, session=None):
        from scrapers.jntukClient import JntukRateLimitedError

        if result_id == "rate-id":
            raise JntukRateLimitedError("JNTUK rate-limited")
        return {
            "status": 200,
            "data": [
                {
                    "htno": roll,
                    "subcode": "R2032421",
                    "subname": "COMPUTER NETWORKS",
                    "credits": "3",
                    "grade": "E",
                }
            ],
        }

    with (
        patch("scrapers.resultScraper.fetch_notifications", new=fake_notifications),
        patch("scrapers.resultScraper.fetch_student_results", new=fake_student_results),
    ):
        result = asyncio.run(scraper.run())

    assert result is not None
    assert [exam["examCode"] for exam in result["results"]] == ["ok-id"]


def _queued_response(_app, roll_number, *args, **kwargs):
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"status": "success", "message": f"queued {roll_number}"},
    )


def _import_result_services():
    try:
        import prisma.types  # noqa: F401
    except ImportError:
        pytest.skip("Prisma client is not generated in this environment")
    from service import (
        getAllResultService,
        getBacklogsService,
        getRequiredCreditsService,
        getResultContrastService,
        getResultsService,
        hardrefresh,
    )
    from service.getClassResults import fetch_class_results

    return SimpleNamespace(
        getAllResultService=getAllResultService,
        getBacklogsService=getBacklogsService,
        getRequiredCreditsService=getRequiredCreditsService,
        getResultContrastService=getResultContrastService,
        getResultsService=getResultsService,
        hardrefresh=hardrefresh,
        fetch_class_results=fetch_class_results,
    )


def test_result_services_project_each_view():
    services = _import_result_services()
    student = _student()
    marks = THREE_ONE_MARKS + THREE_TWO_MARKS
    app = FastAPI()

    async def details(_roll):
        return [student, marks]

    with (
        patch.object(services.getResultsService, "get_details", new=details),
        patch.object(services.getAllResultService, "get_details", new=details),
        patch.object(services.getAllResultService, "getRedisKeyValue", return_value=None),
        patch.object(services.getBacklogsService, "get_details", new=details),
        patch.object(services.getRequiredCreditsService, "get_details", new=details),
        patch.object(services.getResultContrastService, "get_details", new=details),
        patch.object(services.getResultsService.redisConnection, "client", None),
        patch.object(services.getAllResultService.redisConnection, "client", None),
        patch.object(services.getBacklogsService, "getRedisKeyValue", return_value=None),
        patch.object(services.getBacklogsService.redisConnection, "client", None),
        patch.object(services.getRequiredCreditsService.redisConnection, "client", None),
        patch.object(services.getResultContrastService.redisConnection, "client", None),
        patch.object(services.getResultsService, "publish_message", new=AsyncMock()),
    ):
        academic = asyncio.run(services.getResultsService.fetch_results(app, "226Q1A4304"))
        history = asyncio.run(services.getAllResultService.fetch_all_results(app, "226Q1A4304"))
        backlogs = asyncio.run(services.getBacklogsService.fetch_backlogs(app, "226Q1A4304"))
        credits = asyncio.run(
            services.getRequiredCreditsService.fetch_required_credits(app, "226Q1A4304")
        )
        contrast = asyncio.run(
            services.getResultContrastService.fetch_result_contrast(
                app, "226Q1A4304", "226Q1A4305"
            )
        )

    academic_payload = json.loads(academic.body)
    assert academic_payload["results"]["credits"] > 0
    assert history["results"][0]["exams"]
    assert backlogs["results"]["totalBacklogs"] == 0
    assert credits["results"]["totalRequiredCredits"] == 160.0
    assert contrast["studentProfiles"][0]["rollNumber"] == "226Q1A4304"


def test_missing_student_queues_scrape():
    services = _import_result_services()
    app = FastAPI()

    async def missing(_roll):
        return None

    with (
        patch.object(services.getResultsService, "get_details", new=missing),
        patch.object(services.getResultsService.redisConnection, "client", None),
        patch.object(services.getResultsService, "publish_message", new=_queued_response),
    ):
        response = asyncio.run(services.getResultsService.fetch_results(app, "226Q1A4304"))

    assert response.status_code == 202


def test_hard_refresh_invalidates_and_queues():
    services = _import_result_services()
    app = FastAPI()
    redis_client = SimpleNamespace(
        set=MagicMock(),
        delete=MagicMock(),
        srem=MagicMock(),
    )

    with (
        patch.object(services.hardrefresh, "invalidate_all_cache") as invalidate,
        patch.object(services.hardrefresh.redisConnection, "client", redis_client),
        patch.object(services.hardrefresh, "publish_message", new=_queued_response),
    ):
        response = asyncio.run(
            services.hardrefresh.fetch_results_using_hard_refresh(app, "226Q1A4304")
        )

    invalidate.assert_called_once_with("226Q1A4304")
    redis_client.set.assert_called_once()
    assert response.status_code == 202


def test_class_results_renders_academic_view():
    services = _import_result_services()
    app = FastAPI()
    app.state.rabbitmq_connection = SimpleNamespace()
    student = _student()
    student.marks = THREE_TWO_MARKS
    channel = AsyncMock()
    channel.__aenter__ = AsyncMock(
        return_value=SimpleNamespace(
            declare_queue=AsyncMock(
                return_value=SimpleNamespace(
                    declaration_result=SimpleNamespace(message_count=0)
                )
            )
        )
    )
    channel.__aexit__ = AsyncMock(return_value=None)
    app.state.rabbitmq_connection.channel = MagicMock(return_value=channel)

    with (
        patch(
            "service.getClassResults.get_students_details",
            new=AsyncMock(return_value=[student]),
        ),
        patch(
            "service.getClassResults.publish_class_results_message",
            new=AsyncMock(),
        ),
        patch("service.getClassResults.redisConnection.client", None),
    ):
        results = asyncio.run(
            services.fetch_class_results(app, "226Q1A4304", "academicresult")
        )

    assert results[0]["details"]["rollNumber"] == "226Q1A4304"
    assert results[0]["results"]["semesters"]


def test_result_routes_return_each_view(monkeypatch):
    try:
        import prisma.types  # noqa: F401
    except ImportError:
        pytest.skip("Prisma client is not generated in this environment")
    from api.routes import create_routes

    payload = {
        "details": {
            "name": "226Q1A4304",
            "rollNumber": "226Q1A4304",
            "collegeCode": "6Q",
            "fatherName": "",
            "branch": "Artificial Intelligence and Machine Learning",
        },
        "results": studentResultsModel(THREE_TWO_MARKS, False),
    }
    history = {
        "details": payload["details"],
        "results": studentAllResultsModel(THREE_ONE_MARKS),
    }
    backlogs = {
        "details": payload["details"],
        "results": {"semesters": [], "totalBacklogs": 0},
    }
    credits = {
        "details": payload["details"],
        "results": {
            "academicYears": [],
            "totalCredits": 160.0,
            "totalObtainedCredits": 8.0,
            "totalRequiredCredits": 160.0,
        },
    }
    contrast = {
        "studentProfiles": [
            {"rollNumber": "226Q1A4304"},
            {"rollNumber": "226Q1A4305"},
        ],
        "semesters": [],
    }

    async def academic(_app, _roll):
        return payload

    async def all_result(_app, _roll):
        return history

    async def backlog(_app, _roll):
        return backlogs

    async def credit(_app, _roll):
        return credits

    async def compare(_app, first, second):
        return contrast

    async def klass(_app, _roll, _type):
        return [payload]

    async def cmm(_app, roll):
        from fastapi import Response

        return Response(
            content=b"%PDF-sample",
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="CMM-{roll}.pdf"'},
        )

    async def refresh(_app, roll):
        return JSONResponse(
            status_code=202, content={"status": "success", "queued": roll}
        )

    monkeypatch.setattr("api.routes.fetch_results", academic)
    monkeypatch.setattr("api.routes.fetch_all_results", all_result)
    monkeypatch.setattr("api.routes.fetch_backlogs", backlog)
    monkeypatch.setattr("api.routes.fetch_required_credits", credit)
    monkeypatch.setattr("api.routes.fetch_result_contrast", compare)
    monkeypatch.setattr("api.routes.fetch_class_results", klass)
    monkeypatch.setattr("api.routes.fetch_cmm", cmm)
    monkeypatch.setattr("api.routes.fetch_results_using_hard_refresh", refresh)

    app = FastAPI()
    app.include_router(create_routes(app))
    client = TestClient(app)

    assert client.get("/api/getAcademicResult", params={"rollNumber": "226Q1A4304"}).status_code == 200
    assert client.get("/api/getAllResult", params={"rollNumber": "226Q1A4304"}).status_code == 200
    assert client.get("/api/getBacklogs", params={"rollNumber": "226Q1A4304"}).status_code == 200
    assert client.get("/api/getCreditsChecker", params={"rollNumber": "226Q1A4304"}).status_code == 200
    assert client.get(
        "/api/getResultContrast",
        params={"rollNumber1": "226Q1A4304", "rollNumber2": "226Q1A4305"},
    ).status_code == 200
    assert client.get("/api/getClassResults", params={"rollNumber": "226Q1A4304"}).status_code == 200
    cmm_response = client.get("/api/getCMM", params={"rollNumber": "226Q1A4304"})
    assert cmm_response.status_code == 200
    assert cmm_response.headers["content-type"].startswith("application/pdf")
    assert client.get("/api/hardRefresh", params={"rollNumber": "226Q1A4304"}).status_code == 202
