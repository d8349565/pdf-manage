"""Check fresh-case generation and explicitly reproducible regression seeds."""
from benchmark_layout_algorithms import build_case


def test_new_seed_changes_geometry_and_pixels_while_replay_is_identical(tmp_path):
    first=build_case(tmp_path/'first.pdf','ruled_table',0,8917141)
    replay=build_case(tmp_path/'replay.pdf','ruled_table',0,8917141)
    fresh=build_case(tmp_path/'fresh.pdf','ruled_table',0,7720903)
    assert first['parameters']==replay['parameters']
    assert first['image_sha256']==replay['image_sha256']
    assert first['parameters']!=fresh['parameters']
    assert first['regions']!=fresh['regions']
    assert first['image_sha256']!=fresh['image_sha256']
