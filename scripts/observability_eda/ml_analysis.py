"""데이터에서 패턴을 찾아보는 간단한 ML 분석 두 가지.

1. 이상치 탐지: RAG 이벤트 소요시간 중에서 "패턴을 벗어난" 것들을 자동으로 찾는다.
2. 느림 예측: 시간대/이벤트 종류 같은 정보만으로 "이 요청이 느릴지"를 예측할 수
   있는지 본다 — 예측이 잘 되면 "언제/어디서 느려지는지"에 규칙성이 있다는 뜻이고,
   안 되면(정확도가 그냥 찍는 것과 비슷하면) 무작위에 가깝다는 뜻이다. 결과를
   부풀리지 않고 있는 그대로(안 되면 안 된다고) 적는다.
"""

import matplotlib.pyplot as plt
import pandas as pd
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.metrics import classification_report, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import OneHotEncoder


def _save(figure: plt.Figure, output_dir, file_name: str) -> str:
    output_path = output_dir / file_name
    figure.tight_layout()
    figure.savefig(output_path, dpi=110)
    plt.close(figure)
    return str(output_path)


def detect_latency_anomalies(rag_events: pd.DataFrame, output_dir) -> dict:
    """이벤트 종류별로 소요시간 패턴에서 벗어난 건들을 IsolationForest로 찾는다.
    이벤트마다 정상적인 소요시간 범위가 완전히 다르므로(예: bm25_search와
    hybrid_search는 스케일 자체가 다름), 이벤트별로 따로 학습시킨다."""
    plottable = rag_events.dropna(subset=["duration_ms"]).copy()
    plottable["is_anomaly"] = False

    anomaly_summary = []
    for event_name, event_rows in plottable.groupby("event"):
        if len(event_rows) < 30:
            continue  # 너무 적으면 이상치 판단 자체가 의미 없다

        features = event_rows[["duration_ms"]].values
        model = IsolationForest(contamination=0.05, random_state=0)
        predictions = model.fit_predict(features)  # -1 = 이상치, 1 = 정상
        is_anomaly = predictions == -1
        plottable.loc[event_rows.index, "is_anomaly"] = is_anomaly

        anomaly_summary.append({
            "event": event_name,
            "총_건수": len(event_rows),
            "이상치_건수": int(is_anomaly.sum()),
            "이상치_평균_소요시간_ms": round(event_rows.loc[is_anomaly, "duration_ms"].mean(), 1) if is_anomaly.any() else None,
            "정상_평균_소요시간_ms": round(event_rows.loc[~is_anomaly, "duration_ms"].mean(), 1),
        })

    figure, axis = plt.subplots(figsize=(10, 6))
    top_events = plottable["event"].value_counts().head(6).index
    for event_name in top_events:
        event_rows = plottable[plottable["event"] == event_name]
        normal_rows = event_rows[~event_rows["is_anomaly"]]
        anomaly_rows = event_rows[event_rows["is_anomaly"]]
        axis.scatter(normal_rows["created_at"], normal_rows["duration_ms"], s=4, alpha=0.25, label=f"{event_name}(정상)")
        if len(anomaly_rows) > 0:
            axis.scatter(anomaly_rows["created_at"], anomaly_rows["duration_ms"], s=18, color="red", marker="x")
    axis.set_yscale("log")
    axis.set_title("IsolationForest로 찾은 이상 소요시간(빨간 x) — 이벤트별 학습")
    axis.set_ylabel("소요시간(ms, log)")
    axis.legend(fontsize=7, loc="upper left")
    chart_path = _save(figure, output_dir, "21_ml_latency_anomalies.png")

    return {"per_event_summary": anomaly_summary, "chart_path": chart_path}


def predict_slow_requests(rag_events: pd.DataFrame, output_dir) -> dict:
    """시간대(hour)/요일/이벤트 종류/함수 이름만으로 "이 요청이 자기 이벤트 종류
    기준 상위 10% 느린 축에 속하는지"를 예측해본다. duration_ms 자체는 예측
    변수에서 뺀다(그러면 답을 미리 알려주는 셈이라 의미가 없다)."""
    working = rag_events.dropna(subset=["duration_ms", "event"]).copy()
    working["hour"] = working["created_at"].dt.hour
    working["day_of_week"] = working["created_at"].dt.dayofweek

    # 이벤트마다 "느림" 기준(상위 10%)이 다르므로, 이벤트별로 상대적인 기준을 쓴다.
    working["slow_threshold"] = working.groupby("event")["duration_ms"].transform(lambda values: values.quantile(0.9))
    working["is_slow"] = (working["duration_ms"] > working["slow_threshold"]).astype(int)

    feature_columns = ["event", "function", "status", "hour", "day_of_week"]
    working["function"] = working["function"].fillna("(none)")
    features = working[feature_columns].copy()
    features["hour"] = features["hour"].astype(str)
    features["day_of_week"] = features["day_of_week"].astype(str)
    labels = working["is_slow"]

    encoder = OneHotEncoder(handle_unknown="ignore")
    encoded_features = encoder.fit_transform(features)

    features_train, features_test, labels_train, labels_test = train_test_split(
        encoded_features, labels, test_size=0.25, random_state=0, stratify=labels,
    )

    model = RandomForestClassifier(n_estimators=200, max_depth=8, random_state=0, class_weight="balanced")
    model.fit(features_train, labels_train)

    predicted_probabilities = model.predict_proba(features_test)[:, 1]
    predicted_labels = model.predict(features_test)
    auc_score = roc_auc_score(labels_test, predicted_probabilities)
    report = classification_report(labels_test, predicted_labels, output_dict=True)

    # 어떤 정보가 "느림 예측"에 가장 크게 기여했는지 — 특성 중요도 상위 15개.
    feature_names = encoder.get_feature_names_out(feature_columns)
    importance_series = pd.Series(model.feature_importances_, index=feature_names).sort_values(ascending=False).head(15)

    figure, axis = plt.subplots(figsize=(9, 6))
    importance_series.sort_values().plot(kind="barh", ax=axis, color="#805ad5")
    axis.set_title(f"느린 요청 예측 - 특성 중요도 상위 15개 (테스트셋 AUC={auc_score:.3f})")
    axis.set_xlabel("중요도")
    chart_path = _save(figure, output_dir, "22_ml_slow_request_feature_importance.png")

    return {
        "auc": round(auc_score, 4),
        "baseline_positive_rate": round(labels.mean(), 4),
        "classification_report": report,
        "top_features": importance_series.round(4).to_dict(),
        "chart_path": chart_path,
        "interpretation": _interpret_auc(auc_score),
    }


def _interpret_auc(auc_score: float) -> str:
    """AUC 숫자만 던지면 의미가 안 와닿으니, 사람이 읽을 문장으로 바꿔준다.
    부풀리지 않고 있는 그대로 판단한다."""
    if auc_score >= 0.85:
        return "시간대/이벤트 종류만으로도 느림을 꽤 정확히 예측 가능 — 뚜렷한 시간대/종류 패턴이 있다는 뜻."
    if auc_score >= 0.70:
        return "무작위보다는 확실히 낫지만 완벽하진 않음 — 시간대/종류가 어느 정도 영향은 있지만 그게 전부는 아님."
    if auc_score >= 0.55:
        return "거의 무작위(0.5)에 가까움 — 시간대/이벤트 종류만으로는 느림을 설명하기 어려움. 다른 요인(요청 크기, 동시 부하 등)을 봐야 함."
    return "예측력이 사실상 없음(0.5 이하) — 이 특성들로는 느림을 설명 못 함."
