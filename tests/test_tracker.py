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


def test_motion_hint_does_not_shrink_person_box():
    """The screenshot bug: a person standing still, only their phone-hand
    moving. The motion fragment must not overwrite the DNN's full-body box."""
    tr = CentroidTracker(min_hits=1)
    tr.update([Detection(x=400, y=100, w=200, h=500, label="person", conf=0.99)],
              authoritative=True)
    # Between DNN passes: a small moving fragment (the hand) near the center.
    tracks = tr.update([det(480, 300, w=60, h=60)], authoritative=False)
    assert len(tracks) == 1
    t = tracks[0]
    assert t.label == "person"
    assert t.box == (400, 100, 200, 500)  # geometry untouched


def test_motion_fragment_inside_person_does_not_spawn_track():
    """A turning head inside a detected person must not become its own
    'MOTION' object with its own zoom inset."""
    tr = CentroidTracker(min_hits=1, max_distance=50)
    tr.update([Detection(x=400, y=100, w=200, h=500, label="person", conf=0.99)],
              authoritative=True)
    # Fragment far from the track centroid (no match) but inside the box.
    tracks = tr.update([det(430, 130, w=40, h=40)], authoritative=False)
    assert [t.label for t in tracks] == ["person"]

    # A genuinely new mover outside the person still creates a track.
    tr.update([det(900, 400, w=80, h=120)], authoritative=False)
    labels = sorted(t.label for t in tr.tracks.values())
    assert labels == ["motion", "person"]


def test_classified_track_survives_between_dnn_passes():
    """A still person produces no motion hints; the box must stay visible
    across the frames between DNN passes instead of blinking."""
    tr = CentroidTracker(min_hits=1)
    tr.update([Detection(x=10, y=10, w=100, h=300, label="person", conf=0.9)],
              authoritative=True)
    for _ in range(2):  # two motion frames with nothing detected
        tracks = tr.update([], authoritative=False)
        assert [t.label for t in tracks] == ["person"]
    # Motion tracks get no such grace.
    tr2 = CentroidTracker(min_hits=1)
    tr2.update([det(50, 50)])
    assert tr2.update([], authoritative=False) == []


def test_dnn_label_wins_over_motion():
    tr = CentroidTracker(min_hits=1)
    tr.update([det(100, 100, label="motion")])
    tracks = tr.update([det(105, 105, label="person")])
    assert tracks[0].label == "person"
    # A later plain-motion match must not erase the classification.
    tracks = tr.update([det(110, 110, label="motion")])
    assert tracks[0].label == "person"
