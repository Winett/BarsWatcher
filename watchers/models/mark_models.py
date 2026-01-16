from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class RewritingMark(BaseModel):
    mark: str
    date: Optional[str] = None
    attempt: int = 1


class Mark(BaseModel):
    mark: str
    rewriting: List[RewritingMark] = []
    date: Optional[str] = None


class DisciplineMarks(BaseModel):
    name: str
    marks: List[Mark] = []
    mark_PA: Optional[str] = None
    mark_final: Optional[str] = None
