from pathlib import Path

from video_intelligence.artifacts import build_analysis_packet


def test_packet_frame_paths_survive_bundle_relocation():
    worker = Path("/srv/private worker/runs/run-1")
    packet = build_analysis_packet(
        f"Frames live at `{worker}/frames`\nFrame: `{worker}/frames/001.jpg`",
        profile="general",
        run_dir=worker,
    )
    assert "/srv/private worker" not in packet
    assert "`frames/001.jpg`" in packet
    assert "relative to the directory containing this packet" in packet
