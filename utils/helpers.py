from datetime import datetime
from fastapi import HTTPException, Query, status

from config.settings import TELEGRAM_CHAT_ID, TELEGRAM_TOKEN
from utils.logger import telegram_logger


import requests


# JNTUK R16/R19/R20/R23 undergraduate scale. E is a passing grade.
gradestogpa = {
    "O": 10,
    "S": 10,
    "A+": 10,
    "A": 9,
    "B": 8,
    "C": 7,
    "D": 6,
    "E": 5,
    "F": 0,
    "Ab": 0,
    "AB": 0,
    "ABSENT": 0,
    "-": 0,
    "P": 0,
    "COMPLETED": 0,
}
gradestogpabppharamcyr22 = {
    "O": 10,
    "A+": 10,
    "A": 9,
    "B": 8,
    "C": 7,
    "D": 6,
    "E": 5,
    "F": 0,
    "Ab": 0,
    "AB": 0,
    "-": 0,
    "P": 0,
    "COMPLETED": 0,
}
FAILING_GRADES = {"F", "AB", "ABSENT", "-", "MP"}
NON_CREDIT_PASS_GRADES = {"COMPLETED", "P", "SATISFACTORY"}


def format_date(date: datetime) -> str:
    return date.strftime("%Y-%m-%d")


def normalize_grade(grade: str | None) -> str:
    return str(grade or "").strip()


def is_failing_grade(grade: str | None) -> bool:
    return normalize_grade(grade).upper() in FAILING_GRADES


def is_non_credit_pass(grade: str | None) -> bool:
    return normalize_grade(grade).upper() in NON_CREDIT_PASS_GRADES


def getGradeValue(grade, bpharmacyr22):
    normalized = normalize_grade(grade)
    table = gradestogpabppharamcyr22 if bpharmacyr22 else gradestogpa
    if normalized in table:
        return table[normalized]
    return table.get(normalized.upper(), 0)


def isbpharmacyr22(roll_number):
    grad_year = int(roll_number[:2])
    return roll_number[5] == "R" and (
        grad_year >= 23 or (grad_year == 22 and roll_number[4] != "5")
    )


def isGreat(previousGrade, grade):
    if is_failing_grade(previousGrade) and not is_failing_grade(grade):
        return True
    if not is_failing_grade(previousGrade) and is_failing_grade(grade):
        return False
    return getGradeValue(previousGrade, False) <= getGradeValue(grade, False)


def validateRollNo(rollNumber: str = Query(..., min_length=10, max_length=10)):
    """Custom validation function for rollNo"""
    if (
        not rollNumber.isalnum()
    ):  # Checks if rollNo contains only alphanumeric characters
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid roll number. It should contain only letters and numbers.",
        )
    return rollNumber.strip().upper()


def validateconstrastRollNos(
    rollNumber1: str = Query(..., min_length=10, max_length=10),
    rollNumber2: str = Query(..., min_length=10, max_length=10),
):
    """Custom validation function for rollNo"""
    if (
        not rollNumber1.isalnum() or not rollNumber2.isalnum()
    ):  # Checks if rollNo contains only alphanumeric characters
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid roll number. It should contain only letters and numbers.",
        )
    if (
        rollNumber1.strip().upper() == rollNumber2.strip().upper()
    ):  # Checks if rollNo contains only alphanumeric characters
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Both the roll Number are same. Kindly use two diff roll Numbers",
        )

    if (
        rollNumber1.strip().upper()[:2] != rollNumber2.strip().upper()[:2]
        and rollNumber1.strip().upper()[4:8] != rollNumber2.strip().upper()[4:8]
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The two roll numbers should be of same Regulation, year and Branch ",
        )

    return [rollNumber1.strip().upper(), rollNumber2.strip().upper()]


def get_credit_regulation_details(roll_number: str):
    from utils.jntuk import determine_degree, determine_regulation, is_lateral_entry

    credit_regulation_details = {
        "btech": {
            "R16": {
                "Regular": {
                    "1": {"Required": "20", "Total": "40"},
                    "2": {"Required": "47", "Total": "82"},
                    "3": {"Required": "73", "Total": "124"},
                    "4": {"Required": "160", "Total": "160"},
                },
                "Lateral": {
                    "2": {"Required": "21", "Total": "42"},
                    "3": {"Required": "51", "Total": "84"},
                    "4": {"Required": "120", "Total": "120"},
                },
            },
            "R19": {
                "Regular": {
                    "1": {"Required": "20", "Total": "40"},
                    "2": {"Required": "47", "Total": "82"},
                    "3": {"Required": "73", "Total": "124"},
                    "4": {"Required": "160", "Total": "160"},
                },
                "Lateral": {
                    "2": {"Required": "21", "Total": "42"},
                    "3": {"Required": "51", "Total": "84"},
                    "4": {"Required": "120", "Total": "120"},
                },
            },
            "R20": {
                "Regular": {
                    "1": {"Required": "20", "Total": "40"},
                    "2": {"Required": "57", "Total": "82"},
                    "3": {"Required": "87", "Total": "124"},
                    "4": {"Required": "160", "Total": "160"},
                },
                "Lateral": {
                    "2": {"Required": "21", "Total": "42"},
                    "3": {"Required": "59", "Total": "84"},
                    "4": {"Required": "120", "Total": "120"},
                },
            },
            "R23": {
                "Regular": {
                    "1": {"Required": "20", "Total": "40"},
                    "2": {"Required": "57", "Total": "82"},
                    "3": {"Required": "87", "Total": "124"},
                    "4": {"Required": "160", "Total": "160"},
                },
                "Lateral": {
                    "2": {"Required": "21", "Total": "42"},
                    "3": {"Required": "59", "Total": "84"},
                    "4": {"Required": "120", "Total": "120"},
                },
            },
        }
    }

    if len(roll_number) < 10 or determine_degree(roll_number) != "btech":
        return None

    regulation_key = determine_regulation(roll_number)
    entry_type = "Lateral" if is_lateral_entry(roll_number) else "Regular"
    regulation_table = credit_regulation_details["btech"].get(regulation_key)
    if regulation_table is None:
        return None
    return regulation_table[entry_type]


def send_telegram_notification(data):
    if len(data) != 0:
        for item in data:
            message = "<b>🚨 Results have been Released! 🚨</b>\n\n"

            message += f"<b>🎓 {item['title']}</b>\n\n"
            message += (
                f'<b>🔗 Result Link:</b> <a href="{item["link"]}">Click Here</a>\n\n'
            )
            message += f"<b>📅 Released Date:</b> {item['releaseDate']}\n\n"

            message += (
                "💌 <b>Questions or concerns?</b> Reach out to me:\n"
                "- Telegram: @thilak_reddy \n"
                "- Instagram:<a href='https://www.instagram.com/__thilak_reddy__/'>@__thilak_reddy__</a>  \n"
                '- Email: <a href="mailto:thilakreddypothuganti@gmail.com">thilakreddypothuganti@gmail.com</a>\n\n'
                "🌐 <b>More info:</b> <a href='https://jntukresults.edu.in/'>jntukresults.edu.in</a>"
            )
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
            }

            headers = {"Content-Type": "application/json"}

            response = requests.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                telegram_logger.info("Telegram Notification has been Sent")
            else:
                telegram_logger.error("Telegram Notification has been failed to send")
                print("Failed to sent")
