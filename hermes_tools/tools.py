from datetime import datetime, timedelta, timezone

import pandas as pd
from google.cloud import firestore

PROJECT_ID = "<<PROJECT_ID>>"  # 請替換為實際的 GCP 專案 ID
DATABASE = "<<DATABASE_NAME>>"  # 請替換為實際的 Firestore database 名稱
COLLECTION = "<<COLLECTION_NAME>>"  # 請替換為實際的 Firestore collection 名稱
TZ = timezone(timedelta(hours=8))

# --- 植物資料庫 ---
# 由 AI 查詢資料後提供建議的指標
# 可使用 AI 生成更多植物資料，並加入此字典中
PLANT_DB = {
    "龜背竹": {
        "pot_size": "六寸盆",
        "temperature": {"ideal": (20, 28), "warning": (15, 35), "unit": "°C"},
        "humidity": {"ideal": (60, 80), "warning": (40, 90), "unit": "%"},
        "lux": {"ideal": (1000, 10000), "warning": (500, 50000), "unit": "lux"},
        "soil_moist": {"ideal": (40, 70), "warning": (25, 85), "unit": "%"},
        "note": "龜背竹喜歡溫暖潮濕、明亮散射光。六寸盆土壤量少，水分蒸發較快，需更頻繁檢查土壤濕度。",
    },
}

SENSOR_FIELDS = ["temperature", "humidity", "lux", "soil_moist"]


# --- Firestore 讀取底層 ---

def _query_raw(hours: int, limit: int = 200) -> list[dict]:
    hours = max(1, min(hours, 168))
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    client = firestore.Client(project=PROJECT_ID, database=DATABASE)
    docs = (
        client.collection(COLLECTION)
        .where("timestamp", ">=", cutoff)
        .order_by("timestamp", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    records = []
    for doc in docs:
        data = doc.to_dict()
        ts_val = data.get("timestamp", 0)
        if isinstance(ts_val, datetime):
            ts_dt = ts_val.astimezone(TZ)
        else:
            ts_dt = datetime.fromtimestamp(ts_val, tz=timezone.utc).astimezone(TZ)
        records.append({
            "time": ts_dt,
            "temperature": round(data.get("temperature", 0), 1),
            "humidity": round(data.get("humidity", 0), 1),
            "lux": round(data.get("lux", 0), 1),
            "soil_moist": round(data.get("soil_moist", 0), 1),
        })
    records.sort(key=lambda r: r["time"])
    return records


def _query_latest() -> dict:
    client = firestore.Client(project=PROJECT_ID, database=DATABASE)
    docs = (
        client.collection(COLLECTION)
        .order_by("timestamp", direction=firestore.Query.DESCENDING)
        .limit(1)
        .stream()
    )
    for doc in docs:
        return _record_to_dict(doc.to_dict())
    return {"error": "找不到感測器資料"}


def _record_to_dict(data: dict) -> dict:
    ts_val = data.get("timestamp", 0)
    if isinstance(ts_val, datetime):
        ts_dt = ts_val.astimezone(TZ)
    else:
        ts_dt = datetime.fromtimestamp(ts_val, tz=timezone.utc).astimezone(TZ)
    return {
        "time": ts_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "temperature": round(data.get("temperature", 0), 1),
        "humidity": round(data.get("humidity", 0), 1),
        "lux": round(data.get("lux", 0), 1),
        "soil_moist": round(data.get("soil_moist", 0), 1),
    }


# --- DataFrame 處理 ---

def _records_to_df(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(records)
    df["time"] = pd.to_datetime(df["time"])
    df = df.set_index("time").sort_index()
    for col in SENSOR_FIELDS:
        if col in df.columns:
            df[col] = df[col].astype(float)
    return df


def _compute_trend(series: pd.Series) -> dict:
    """用簡單線性回歸計算趨勢方向與變化速率（每小時）。"""
    n = len(series.dropna())
    if n < 3:
        return {"direction": "資料不足", "rate_per_hour": 0}
    x = ((series.index - series.index[0]).total_seconds() / 3600.0).to_numpy()
    y = series.to_numpy()
    x_mean = x.mean()
    y_mean = y.mean()
    slope = ((x - x_mean) * (y - y_mean)).sum() / ((x - x_mean) ** 2).sum()
    slope = round(slope, 2)
    if abs(slope) < 0.05:
        direction = "穩定"
    elif slope > 0:
        direction = "上升"
    else:
        direction = "下降"
    return {"direction": direction, "rate_per_hour": slope}


def _describe_field(df: pd.DataFrame, field: str) -> dict:
    col = df[field].dropna()
    if col.empty:
        return {}
    q25, q50, q75 = col.quantile([0.25, 0.5, 0.75])
    trend = _compute_trend(col)
    return {
        "current": round(col.iloc[-1], 1),
        "avg": round(col.mean(), 1),
        "std": round(col.std(), 1),
        "min": round(col.min(), 1),
        "max": round(col.max(), 1),
        "q25": round(q25, 1),
        "median": round(q50, 1),
        "q75": round(q75, 1),
        "trend": trend["direction"],
        "trend_rate_per_hour": trend["rate_per_hour"],
    }


def _aggregate_hourly(df: pd.DataFrame) -> list[dict]:
    """依半小時彙總，回傳精簡的聚合資料。"""
    if df.empty:
        return []
    aggregated = df.resample("30min").mean(numeric_only=True).round(1)
    aggregated = aggregated.dropna(how="all")
    return [
        {
            "time": t.strftime("%m/%d %H:%M"),
            **{f: v for f in SENSOR_FIELDS if f in aggregated.columns and not pd.isna(v := aggregated.at[t, f])},
        }
        for t in aggregated.index
    ]


# --- 對外工具 ---

def get_latest_sensor_data() -> dict:
    """取得最新一筆感測器資料，包含溫度、濕度、光照、土壤濕度。"""
    return _query_latest()


def get_sensor_history(hours: int = 24) -> list[dict]:
    """查詢過去 N 小時的感測器歷史資料，自動以小時聚合後回傳。

    Args:
        hours: 往回查詢的小時數 (1-168)
    """
    records = _query_raw(hours)
    if not records:
        return []
    df = _records_to_df(records)
    return _aggregate_hourly(df)


def get_sensor_summary(hours: int = 24) -> dict:
    """取得感測器資料的統計摘要：平均值、標準差、四分位數、趨勢方向等豐富資訊。

    Args:
        hours: 往回查詢的小時數 (1-168)
    """
    records = _query_raw(hours)
    if not records:
        return {"error": "找不到感測器資料"}
    df = _records_to_df(records)
    return {
        "period_hours": hours,
        "record_count": len(df),
        "time_range": {
            "from": df.index[0].strftime("%Y-%m-%d %H:%M"),
            "to": df.index[-1].strftime("%Y-%m-%d %H:%M"),
        },
        "temperature": _describe_field(df, "temperature"),
        "humidity": _describe_field(df, "humidity"),
        "lux": _describe_field(df, "lux"),
        "soil_moist": _describe_field(df, "soil_moist"),
    }


def get_sensor_trend(hours: int = 24) -> dict:
    """專門分析各感測指標的變化趨勢：目前方向、每小時變化速率、變動幅度。

    Args:
        hours: 往回查詢的小時數 (1-168)
    """
    records = _query_raw(hours)
    if not records:
        return {"error": "找不到感測器資料"}
    df = _records_to_df(records)
    result: dict = {"period_hours": hours, "record_count": len(df)}
    for field in SENSOR_FIELDS:
        if field not in df.columns:
            continue
        col = df[field].dropna()
        if len(col) < 3:
            result[field] = {"data_points": len(col), "error": "資料點不足，無法分析趨勢"}
            continue
        trend = _compute_trend(col)
        start_val = round(col.iloc[0], 1)
        end_val = round(col.iloc[-1], 1)
        total_change = round(end_val - start_val, 1)
        result[field] = {
            "start_value": start_val,
            "end_value": end_val,
            "total_change": total_change,
            "trend_direction": trend["direction"],
            "rate_per_hour": trend["rate_per_hour"],
        }
    return result


# --- 植物相關 ---

def get_available_plants() -> list[dict]:
    """列出所有已登錄的植物及其盆器資訊。"""
    return [
        {"name": name, "pot_size": info["pot_size"]}
        for name, info in PLANT_DB.items()
    ]


def get_plant_info(plant_name: str = "龜背竹") -> dict:
    """查詢植物的基本資訊與適合的生長條件（溫度、濕度、光照、土壤濕度理想範圍）。

    Args:
        plant_name: 植物名稱，用 get_available_plants 可查看支援清單
    """
    info = PLANT_DB.get(plant_name)
    if info:
        return {"plant": plant_name, **info}
    return {"error": f"找不到「{plant_name}」的資料", "available_plants": list(PLANT_DB.keys())}


def evaluate_plant_status(plant_name: str = "龜背竹") -> dict:
    """取得最新感測資料，根據指定植物的適合生長條件進行評估，回傳各項指標狀態與建議。

    Args:
        plant_name: 植物名稱，例如「龜背竹」。用 get_available_plants 可查看支援清單。
    """
    latest = get_latest_sensor_data()
    if "error" in latest:
        return {"error": "無法取得感測資料，無法評估植物狀態"}

    info = PLANT_DB.get(plant_name)
    if not info:
        return {"error": f"找不到「{plant_name}」的資料", "available_plants": list(PLANT_DB.keys())}

    def _check(value, range_def):
        ideal_lo, ideal_hi = range_def["ideal"]
        warn_lo, warn_hi = range_def["warning"]
        if ideal_lo <= value <= ideal_hi:
            return "理想", None
        if warn_lo <= value <= warn_hi:
            if value < ideal_lo:
                return "偏低", f"建議調整至 {ideal_lo}-{ideal_hi} {range_def['unit']}"
            return "偏高", f"建議調整至 {ideal_lo}-{ideal_hi} {range_def['unit']}"
        if value < warn_lo:
            return "危險（過低）", f"數值過低，需立即處理！目標範圍 {ideal_lo}-{ideal_hi} {range_def['unit']}"
        return "危險（過高）", f"數值過高，需立即處理！目標範圍 {ideal_lo}-{ideal_hi} {range_def['unit']}"

    temp_status, temp_advice = _check(latest["temperature"], info["temperature"])
    hum_status, hum_advice = _check(latest["humidity"], info["humidity"])
    lux_status, lux_advice = _check(latest["lux"], info["lux"])
    soil_status, soil_advice = _check(latest["soil_moist"], info["soil_moist"])

    return {
        "plant": plant_name,
        "pot_size": info["pot_size"],
        "time": latest["time"],
        "temperature": {
            "value": latest["temperature"],
            "unit": "°C",
            "ideal_range": f'{info["temperature"]["ideal"][0]}-{info["temperature"]["ideal"][1]}°C',
            "status": temp_status,
            "advice": temp_advice,
        },
        "humidity": {
            "value": latest["humidity"],
            "unit": "%",
            "ideal_range": f'{info["humidity"]["ideal"][0]}-{info["humidity"]["ideal"][1]}%',
            "status": hum_status,
            "advice": hum_advice,
        },
        "lux": {
            "value": latest["lux"],
            "unit": "lux",
            "ideal_range": f'{info["lux"]["ideal"][0]}-{info["lux"]["ideal"][1]} lux',
            "status": lux_status,
            "advice": lux_advice,
        },
        "soil_moist": {
            "value": latest["soil_moist"],
            "unit": "%",
            "ideal_range": f'{info["soil_moist"]["ideal"][0]}-{info["soil_moist"]["ideal"][1]}%',
            "status": soil_status,
            "advice": soil_advice,
        },
        "note": info["note"],
    }
