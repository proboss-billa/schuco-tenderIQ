"""Pydantic models for request validation."""

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)


class CreateProjectRequest(BaseModel):
    project_name: str = Field(min_length=1, max_length=255)
    project_description: str = ""
    project_type: str = Field(default="commercial", pattern=r"^(commercial|residential)$")


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=5000)
