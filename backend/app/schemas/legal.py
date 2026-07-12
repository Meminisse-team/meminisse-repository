from pydantic import BaseModel


class DisclosuresRead(BaseModel):
    non_medical_service: str
