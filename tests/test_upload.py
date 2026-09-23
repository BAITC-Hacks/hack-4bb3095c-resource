from engine.core import compute_orders
from engine.loaders import load_workbook_upload, template_bytes


def test_template_roundtrip_gives_orders_for_every_supplier():
    data = load_workbook_upload(template_bytes())
    result = compute_orders(**data)
    orders = result.orders
    assert set(orders.supplier) == {"S1", "S2"}
    assert (orders.rec_qty >= 0).all()
    assert orders.reason.str.len().gt(0).all()
