import json
from pathlib import Path

from .dataset_schema import EvaluationDataset

DATASET_PATH = Path(__file__).resolve().parents[2] / "data" / "evaluation_datasets.json"


def load_datasets() -> list[EvaluationDataset]:
    if not DATASET_PATH.exists():
        return []
    data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    return [EvaluationDataset.model_validate(item) for item in data]


def save_dataset(dataset: EvaluationDataset) -> EvaluationDataset:
    datasets = load_datasets()
    key = (dataset.dataset_id, dataset.version)
    if any((item.dataset_id, item.version) == key for item in datasets):
        raise ValueError(f"dataset_id={dataset.dataset_id!r}, version={dataset.version!r}이 이미 있습니다")
    datasets.append(dataset)
    DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATASET_PATH.write_text(
        json.dumps([item.model_dump() for item in datasets], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return dataset
