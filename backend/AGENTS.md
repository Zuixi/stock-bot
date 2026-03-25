# backend

backend service used to provide unified restful api for the frontend service, and fetch and manage all data from the exchanges.

## Componenes
- app: fastAPI service to provide restful api for the frontend service.
- crons: cron jobs to fetch and manage data from the exchanges.
- docs: documentation for the backend service
- tests: tests for the backend service
- core: core fetching module to get datas.
- fetchers: unified fetcher client for 3 exchanges.
- schemas: pydantic models for the backend service.


## Tech Stack
- Using FastAPI to build the restful api for the frontend service.
- Using SQLModel to build the database models.
- Using Redis to cache the data.
- Using RabbitMQ to manage the data flow.
- Using Docker and docker-compose to manage the service.
