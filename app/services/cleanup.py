"""정리할 결정 찾기 (순수 함수): 상담은 했는데 뒤처리가 안 된 건.

- 판정 전에 멈춘 상담: 이어가거나 지운다
- '샀어요/팔았어요'라고 했는데 체결이 없음: 체결을 적거나 '안 샀음'으로 바꾼다
  (증권사 CSV를 기다리는 며칠은 봐 준다)
- 판정 뒤 실행/보류를 안 고름: 고른다
이런 건이 쌓이면 성적표·리포트가 실제보다 좋거나 나쁘게 나온다.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models import Decision

UNFINISHED_AFTER = timedelta(days=1)  # 어제 시작한 상담까지는 아직 진행 중일 수 있다
UNLINKED_AFTER = timedelta(days=3)  # 체결은 증권사 CSV로 며칠 뒤 들어올 수 있다
UNDECIDED_AFTER = timedelta(days=1)


@dataclass(frozen=True)
class Loose:
    unfinished: list["Decision"] = field(default_factory=list)
    unlinked: list["Decision"] = field(default_factory=list)
    undecided: list["Decision"] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.unfinished) + len(self.unlinked) + len(self.undecided)


def find_loose(decisions: Iterable["Decision"], today: date) -> Loose:
    out = Loose()
    for d in sorted(decisions, key=lambda d: (d.decided_on, d.id), reverse=True):
        if d.source != "consult":
            continue
        age = today - d.decided_on
        if d.verdict is None:
            if age >= UNFINISHED_AFTER:
                out.unfinished.append(d)
        elif d.user_action == "executed" and not d.fills:
            if age >= UNLINKED_AFTER:
                out.unlinked.append(d)
        elif d.user_action == "pending" and age >= UNDECIDED_AFTER:
            out.undecided.append(d)
    return out
