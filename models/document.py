from pydantic import BaseModel, Field

class Document(BaseModel):
    title: str = Field(..., example="Sample Document")
    content: str = Field(..., example="This is a sample document content.")