"""Splituri temporale walk-forward fără shuffle și fără leakage."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WalkForwardFold:
    train: slice
    validation: slice
    test: slice


def walk_forward_splits(
    sample_count: int,
    *,
    train_size: int,
    validation_size: int,
    test_size: int,
    step_size: int | None = None,
    anchored_train: bool = False,
) -> list[WalkForwardFold]:
    """Returnează fold-uri strict cronologice ``train < validation < test``.

    În modul rolling, train-ul are lungime fixă. Cu ``anchored_train=True``,
    începutul rămâne 0 și fereastra de train crește la fiecare pas.
    """
    sizes = (sample_count, train_size, validation_size, test_size)
    if any(not isinstance(value, int) or value <= 0 for value in sizes):
        raise ValueError("sample_count și dimensiunile ferestrelor trebuie să fie întregi pozitivi")
    if step_size is None:
        step_size = test_size
    if not isinstance(step_size, int) or step_size <= 0:
        raise ValueError("step_size trebuie să fie un întreg pozitiv")

    required = train_size + validation_size + test_size
    if sample_count < required:
        return []

    folds = []
    offset = 0
    while offset + required <= sample_count:
        train_start = 0 if anchored_train else offset
        train_stop = offset + train_size
        validation_stop = train_stop + validation_size
        test_stop = validation_stop + test_size
        folds.append(WalkForwardFold(
            train=slice(train_start, train_stop),
            validation=slice(train_stop, validation_stop),
            test=slice(validation_stop, test_stop),
        ))
        offset += step_size
    return folds
