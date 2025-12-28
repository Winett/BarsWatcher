from pydantic import BaseModel, Field

class Mark(BaseModel):
    discipline: str | None = None
    marks: list["MarkKM"] | None = Field(default_factory=list)
    mark_PA: str | None = ''
    mark_final: str | None = ''


class RewriteMark(BaseModel):
    mark: str | None = '' # Оценка за сам КМ

    date_of_mark: str | None = '' # Дата оценки

class MarkKM(RewriteMark):
    # mark: str | None = '' # Оценка за сам КМ
    # date_of_mark: str | None = '' # Дата оценки

    rewriting: list["RewriteMark"] = Field(default_factory=list) #Оценки за переписывание
