"""Time-window labelling and annual expanding walk-forward folds."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class WalkForwardFold:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    predict_start: pd.Timestamp
    predict_end: pd.Timestamp


def split_label(date: pd.Timestamp, config: dict) -> str:
    value = pd.Timestamp(date)
    if pd.Timestamp(config["development_start"]) <= value <= pd.Timestamp(config["development_end"]):
        return "DEVELOPMENT"
    if pd.Timestamp(config["validation_start"]) <= value <= pd.Timestamp(config["validation_end"]):
        return "VALIDATION"
    if pd.Timestamp(config["holdout_start"]) <= value <= pd.Timestamp(config["holdout_end"]):
        return "HOLDOUT_SEALED"
    if value >= pd.Timestamp(config["live_start"]):
        return "LIVE_INCOMPLETE"
    return "WARMUP"


def label_splits(frame: pd.DataFrame, config: dict) -> pd.DataFrame:
    result = frame.copy()
    result["split"] = [split_label(value, config) for value in result["date"]]
    return result


def annual_validation_folds(config: dict) -> list[WalkForwardFold]:
    start = pd.Timestamp(config["validation_start"])
    end = pd.Timestamp(config["validation_end"])
    folds: list[WalkForwardFold] = []
    for year in range(int(start.year), int(end.year) + 1):
        predict_start = max(start, pd.Timestamp(year=year, month=1, day=1))
        predict_end = min(end, pd.Timestamp(year=year, month=12, day=31))
        fold = WalkForwardFold(
            train_start=pd.Timestamp(config["development_start"]),
            train_end=predict_start - pd.Timedelta(days=1),
            predict_start=predict_start,
            predict_end=predict_end,
        )
        if fold.train_end >= fold.predict_start:
            raise ValueError("walk-forward train and prediction windows overlap")
        folds.append(fold)
    return folds


def walk_forward_predict(frame: pd.DataFrame, model_factory, config: dict) -> pd.Series:
    """Fit/predict each validation year; development predictions are direct."""
    output = pd.Series(pd.NA, index=frame.index, dtype="string")
    development = frame["date"].between(config["development_start"], config["development_end"])
    development_model = model_factory().fit(frame.loc[development])
    output.loc[development] = development_model.predict(frame.loc[development])

    for fold in annual_validation_folds(config):
        train = frame["date"].between(fold.train_start, fold.train_end)
        predict = frame["date"].between(fold.predict_start, fold.predict_end)
        model = model_factory().fit(frame.loc[train])
        output.loc[predict] = model.predict(frame.loc[predict])
    return output


def assert_holdout_allowed(config: dict, confirm_holdout: bool, git_dirty: bool) -> None:
    if config.get("protocol_status") != "FROZEN":
        raise ValueError("holdout requires protocol_status=FROZEN")
    if not confirm_holdout:
        raise ValueError("holdout requires explicit --confirm-holdout")
    if git_dirty:
        raise ValueError("holdout requires a clean committed worktree")
