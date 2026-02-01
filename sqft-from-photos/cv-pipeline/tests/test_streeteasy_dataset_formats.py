import json

from cv_pipeline.dataset.streeteasy import load_streeteasy_dataset


def test_load_dataset_legacy_examples_defaults_downloads(tmp_path):
    dataset = {
        "examples": [
            {
                "listingUrl": "https://streeteasy.com/building/123-main-street/4a",
                "sqft": 777,
            }
        ]
    }
    (tmp_path / "downloads").mkdir()
    (tmp_path / "dataset.json").write_text(json.dumps(dataset), encoding="utf-8")

    out = load_streeteasy_dataset(tmp_path / "dataset.json")
    assert len(out) == 1
    assert out[0].listing_id == "building__123-main-street__4a"
    assert out[0].images_dir == tmp_path / "downloads" / "building__123-main-street__4a"


def test_load_dataset_eval_listings_uses_photos_dir(tmp_path):
    dataset = {
        "dataset_info": {"name": "streeteasy_eval_dataset"},
        "listings": [
            {
                "id": "listing_001",
                "url": "https://streeteasy.com/building/1-ocean-drive/w20c",
                "has_sqft_data": True,
                "sqft": 888,
                "photo_count": 2,
                "photo_paths": ["photos/listing_001/photo_00.jpg", "photos/listing_001/photo_01.jpg"],
            }
        ],
    }
    (tmp_path / "photos" / "listing_001").mkdir(parents=True)
    (tmp_path / "listings.json").write_text(json.dumps(dataset), encoding="utf-8")

    out = load_streeteasy_dataset(tmp_path / "listings.json")
    assert len(out) == 1
    assert out[0].listing_id == "listing_001"
    assert out[0].sqft == 888
    assert out[0].images_dir == tmp_path / "photos" / "listing_001"
