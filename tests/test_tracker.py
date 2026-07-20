from allseeingeye.tracker import CentroidTracker, Detection


def det(x, y, w=40, h=80, label="motion"):
    return Detection(x=x, y=y, w=w, h=h, label=label)


def test_track_identity_persists_across_movement():
    tr = CentroidTracker(min_hits=1)
    t1 = tr.update([det(100, 100)])
    assert len(t1) == 1
    tid = t1[0].id

    # Object moves a little each frame — same track must follow it.
    for i in range(1, 10):
        tracks = tr.update([det(100 + i * 10, 100 + i * 5)])
        assert len(tracks) == 1
        assert tracks[0].id == tid

    assert len(tr.tracks[tid].trail) == 10


def test_min_hits_suppresses_one_frame_noise():
    tr = CentroidTracker(min_hits=3)
    assert tr.update([det(50, 50)]) == []
    assert tr.update([det(52, 52)]) == []
    assert len(tr.update([det(54, 54)])) == 1


def test_two_objects_get_distinct_ids():
    tr = CentroidTracker(min_hits=1)
    tracks = tr.update([det(0, 0), det(500, 500)])
    assert len(tracks) == 2
    assert tracks[0].id != tracks[1].id

    # They keep their identities as both move.
    tracks2 = tr.update([det(10, 10), det(510, 510)])
    by_pos = sorted(tracks2, key=lambda t: t.box[0])
    assert by_pos[0].id != by_pos[1].id


def test_track_expires_after_max_missed():
    tr = CentroidTracker(min_hits=1, max_missed=2)
    tr.update([det(100, 100)])
    for _ in range(4):
        tr.update([])
    assert tr.tracks == {}


def test_dnn_label_wins_over_motion():
    tr = CentroidTracker(min_hits=1)
    tr.update([det(100, 100, label="motion")])
    tracks = tr.update([det(105, 105, label="person")])
    assert tracks[0].label == "person"
    # A later plain-motion match must not erase the classification.
    tracks = tr.update([det(110, 110, label="motion")])
    assert tracks[0].label == "person"
