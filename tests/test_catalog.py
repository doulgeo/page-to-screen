from page_to_screen.catalog import (
    AUTHORS_CATALOG,
    CLASSICS_CATALOG,
    Catalog,
    parse_extra,
    to_meta,
)


def film(i, name="Film", authors=("Someone",), year=2000, popularity=0, work_year=1950):
    return {
        "id": f"tt{i:07d}",
        "name": name,
        "year": year,
        "popularity": popularity,
        "authors": list(authors),
        "works": [{"title": "Book", "year": work_year}],
    }


FILMS = [
    film(1, "War and Peace", ["Leo Tolstoy"], 1956, 60, 1869),
    film(2, "Anna Karenina", ["Leo Tolstoy"], 2012, 50, 1878),
    film(3, "The Shining", ["Stephen King"], 1980, 90, 1977),
    film(4, "The Constant Gardener", ["John le Carré"], 2005, 40, 2001),
    film(5, "Undated", ["Nobody"], None, 1, None),
]


def ids(metas):
    return [m["id"] for m in metas]


def test_parse_extra():
    assert parse_extra("genre=Leo%20Tolstoy&skip=100") == {"genre": "Leo Tolstoy", "skip": "100"}
    assert parse_extra("search=pride%20%26%20prejudice") == {"search": "pride & prejudice"}
    assert parse_extra("") == {}


def test_default_is_all_sorted_by_popularity():
    assert ids(Catalog(FILMS).query(AUTHORS_CATALOG, {})) == [
        "tt0000003", "tt0000001", "tt0000002", "tt0000004", "tt0000005",
    ]


def test_sort_by_year_puts_newest_first_and_undated_last():
    assert ids(Catalog(FILMS, sort="year").query(AUTHORS_CATALOG, {})) == [
        "tt0000002", "tt0000004", "tt0000003", "tt0000001", "tt0000005",
    ]


def test_genre_filters_by_author():
    catalog = Catalog(FILMS)
    assert ids(catalog.query(AUTHORS_CATALOG, {"genre": "Leo Tolstoy"})) == ["tt0000001", "tt0000002"]
    assert catalog.query(AUTHORS_CATALOG, {"genre": "Nobody Else"}) == []


def test_classics_are_works_published_before_1929():
    catalog = Catalog(FILMS + [film(6, "Edge", work_year=1929), film(7, "Just in", work_year=1928)])
    assert ids(catalog.query(CLASSICS_CATALOG, {})) == ["tt0000001", "tt0000002", "tt0000007"]
    assert ids(catalog.query(CLASSICS_CATALOG, {"genre": "Stephen King"})) == []


def test_search_matches_film_or_author_ignoring_case_and_accents():
    catalog = Catalog(FILMS)
    assert ids(catalog.query(AUTHORS_CATALOG, {"search": "SHINING"})) == ["tt0000003"]
    assert ids(catalog.query(AUTHORS_CATALOG, {"search": "tolstoy"})) == ["tt0000001", "tt0000002"]
    assert ids(catalog.query(AUTHORS_CATALOG, {"search": "le carre"})) == ["tt0000004"]
    assert catalog.query(AUTHORS_CATALOG, {"search": "zzz"}) == []


def test_pagination_is_100_per_page():
    catalog = Catalog([film(i, popularity=1000 - i) for i in range(250)])
    assert len(catalog.query(AUTHORS_CATALOG, {})) == 100
    page2 = catalog.query(AUTHORS_CATALOG, {"skip": "100"})
    assert page2[0]["id"] == "tt0000100" and len(page2) == 100
    assert len(catalog.query(AUTHORS_CATALOG, {"skip": "200"})) == 50
    assert catalog.query(AUTHORS_CATALOG, {"skip": "300"}) == []
    assert len(catalog.query(AUTHORS_CATALOG, {"skip": "junk"})) == 100


def test_meta_shape():
    assert to_meta(FILMS[0]) == {
        "id": "tt0000001",
        "type": "movie",
        "name": "War and Peace",
        "poster": "https://images.metahub.space/poster/medium/tt0000001/img",
        "posterShape": "poster",
        "releaseInfo": "1956",
        "description": "Based on “Book” (1869) by Leo Tolstoy.",
    }
    assert "releaseInfo" not in to_meta(FILMS[4])


def test_author_options_list_curated_first_then_most_adapted():
    options = Catalog(FILMS).author_options(AUTHORS_CATALOG)
    assert options[:3] == ["Leo Tolstoy", "John le Carré", "Stephen King"]
    assert "Nobody" in options
    assert Catalog(FILMS).author_options(CLASSICS_CATALOG) == ["Leo Tolstoy"]
