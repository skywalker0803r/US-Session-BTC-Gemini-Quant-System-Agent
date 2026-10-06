import base64
import json
from datetime import datetime, timezone

from quant_agent.adapters import GateIoAdapter, MarketDataPipeline, MarketDataSource
from quant_agent.models import MarketSnapshot


class StaticSource:
    def __init__(self, snapshot: MarketSnapshot) -> None:
        self.snapshot_data = snapshot

    def snapshot(self, *, timestamp: datetime) -> MarketSnapshot:
        return MarketSnapshot(
            timestamp=timestamp,
            source=self.snapshot_data.source,
            instrument=self.snapshot_data.instrument,
            price=self.snapshot_data.price,
            kline_base64=self.snapshot_data.kline_base64,
            orderbook_imbalance=self.snapshot_data.orderbook_imbalance,
            cvd=self.snapshot_data.cvd,
            macro_summary=self.snapshot_data.macro_summary,
            news=self.snapshot_data.news,
            vix=self.snapshot_data.vix,
            dxy=self.snapshot_data.dxy,
            nq_es_spread=self.snapshot_data.nq_es_spread,
            quality_flags=self.snapshot_data.quality_flags,
            data_sources=self.snapshot_data.data_sources,
        )


def test_gate_adapter_builds_complete_bitcoin_snapshot(monkeypatch) -> None:
    responses = {
        "/price": {"price": "50000.0"},
        "/candles": [
            {
                "timestamp": 1_000,
                "open": 49_000,
                "high": 50_000,
                "low": 48_500,
                "close": 49_500,
                "volume": 10,
            },
            {
                "timestamp": 2_000,
                "open": 49_500,
                "high": 50_100,
                "low": 49_400,
                "close": 49_900,
                "volume": 20,
            },
        ],
        "/order_book": [
            {"price": "49990", "size": 100},
            {"price": "50010", "size": 50},
        ],
    }

    requests = []

    def get(path, **kwargs):
        requests.append((path, kwargs))
        return responses[path]

    adapter = GateIoAdapter(api_key="key", secret="secret")
    adapter._get = get  # type: ignore[assignment]

    snapshot = adapter.market_snapshot(timestamp=datetime.now(timezone.utc))

    assert snapshot.price == 50_000.0
    assert len(requests) == 3
    assert snapshot.vix is None
    assert snapshot.dxy is None
    assert snapshot.nq_es_spread is None
    assert snapshot.orderbook_imbalance is not None
    assert snapshot.cvd is not None
    assert snapshot.quality_flags == ("KLINE_DATA_PRESENT", "ORDER_BOOK_DATA_PRESENT")
    candles = json.loads(base64.b64decode(snapshot.kline_base64))
    assert len(candles) == 2
    assert candles[-1]["close"] == 49_900


def test_market_data_pipeline_merges_all_sources() -> None:
    now = datetime.now(timezone.utc)
    bitcoin = MarketSnapshot(
        timestamp=now,
        source="gateio",
        instrument="BTC/USDT:USDT",
        price=50_000.0,
        kline_base64=base64.b64encode(json.dumps([{"close": 49_500}]).encode()).decode(),
        orderbook_imbalance=0.01,
        cvd=0.02,
        macro_summary="Inflation expectations remain elevated",
        quality_flags=("KLINE_DATA_PRESENT", "ORDER_BOOK_DATA_PRESENT"),
        data_sources=("gateio",),
    )
    vix = MarketSnapshot(
        timestamp=now, source="vix", instrument="VIX", price=20.0,
        quality_flags=("VIX_DATA_PRESENT",), data_sources=("vix",),
    )
    dxy = MarketSnapshot(
        timestamp=now, source="dxy", instrument="DXY", price=104.0,
        quality_flags=("DXY_DATA_PRESENT",), data_sources=("dxy",),
    )
    nq_es = MarketSnapshot(
        timestamp=now, source="nq-es", instrument="NQ/ES", price=0.5,
        quality_flags=("NQ_ES_DATA_PRESENT",), data_sources=("nq-es",),
    )
    news = MarketSnapshot(
        timestamp=now, source="news", instrument="NEWS", price=None,
        news=({"title": "USD inflation", "sentiment": "high"},),
        quality_flags=("NEWS_DATA_PRESENT",), data_sources=("news",),
    )

    pipeline = MarketDataPipeline(
        bitcoin=StaticSource(bitcoin),
        vix=StaticSource(vix),
        dxy=StaticSource(dxy),
        nq_es=StaticSource(nq_es),
        news=StaticSource(news),
    )
    snapshot = pipeline.collect(timestamp=now)

    assert snapshot.price == 50_000.0
    assert snapshot.vix == 20.0
    assert snapshot.dxy == 104.0
    assert snapshot.nq_es_spread == 0.5
    assert snapshot.news == ({"title": "USD inflation", "sentiment": "high"},)
    assert snapshot.data_sources == ("gateio", "vix", "dxy", "nq-es", "news")
    assert snapshot.quality_flags == (
        "KLINE_DATA_PRESENT", "ORDER_BOOK_DATA_PRESENT", "VIX_DATA_PRESENT",
        "DXY_DATA_PRESENT", "NQ_ES_DATA_PRESENT", "NEWS_DATA_PRESENT",
    )


def test_market_data_pipeline_rejects_incomplete_required_data() -> None:
    now = datetime.now(timezone.utc)
    bitcoin = MarketSnapshot(
        timestamp=now, source="gateio", instrument="BTC/USDT:USDT", price=None,
        quality_flags=(), data_sources=("gateio",),
    )
    pipeline = MarketDataPipeline(bitcoin=StaticSource(bitcoin))

    try:
        pipeline.collect(timestamp=now)
    except ValueError as exc:
        assert "price" in str(exc).lower()
    else:
        raise AssertionError("Missing Bitcoin price must be rejected")
