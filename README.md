# JNTUK Results BACKEND

This FastAPI-based service provides access to JNTUK student results, academic records, and backlog details from [jntukresults.edu.in](https://jntukresults.edu.in/). It integrates with PostgreSQL, Redis, and RabbitMQ for efficient data handling and messaging.

[![License](https://img.shields.io/github/license/Bannysukumar/jntuk-backend)](https://github.com/Bannysukumar/jntuk-backend/blob/main/LICENSE) [![Stars](https://img.shields.io/github/stars/Bannysukumar/jntuk-backend)](https://github.com/Bannysukumar/jntuk-backend/stargazers) [![Last commit](https://img.shields.io/github/last-commit/Bannysukumar/jntuk-backend)](https://github.com/Bannysukumar/jntuk-backend/commits/main) [![Build](https://img.shields.io/github/actions/workflow/status/Bannysukumar/jntuk-backend/deploy.yml)](https://github.com/Bannysukumar/jntuk-backend/actions)

## Overview

This FastAPI-based service provides access to JNTUK student results, academic records, and backlog details from [jntukresults.edu.in](https://jntukresults.edu.in/). It integrates with PostgreSQL, Redis, and RabbitMQ for efficient data handling and messaging.


What is actually in the repository: `.github/`, `api/`, `assests/`, `chatbot/`, `config/`, `data/`. GitHub reports the primary language as Python.

## Tech Stack

| Technology | Where it shows up |
|---|---|
| Python | Application or script code |
| API routes | Server endpoints in the api directory |

## Project Structure

```text
jntuk-backend/
├── .github/
├── api/
├── assests/
├── chatbot/
├── config/
├── data/
├── database/
├── messaging/
├── prisma/
├── scrapers/
├── service/
├── static/
├── subscriptions/
├── tests/
├── .dockerignore
├── .env.example
├── CLAUDE.md
├── DEPLOYMENT.md
├── Dockerfile
├── RUNBOOK.md
├── SECURITY.md
├── architecture.md
├── docker-compose.yml
├── entrypoint.sh
├── locustfile.py
├── main.py
```

## Getting Started

```bash
git clone https://github.com/Bannysukumar/jntuk-backend.git
cd jntuk-backend
pip install -r requirements.txt
# Copy .env.example to .env and fill in the values that file lists.
```

## API

Endpoint files present in `api/`:

- `api/routes.py`

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

## License

Licensed under GPL-3.0. See [LICENSE](LICENSE).

## Author

[Banny Sukumar](https://github.com/Bannysukumar)

- GitHub: [@Bannysukumar](https://github.com/Bannysukumar)
- Portfolio: [adepu-sukumar.vercel.app](https://adepu-sukumar.vercel.app/)
- LinkedIn: [Adepu Sukumar](https://www.linkedin.com/in/adepu-sukumar-59b423351)
