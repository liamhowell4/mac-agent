from tooleval.tools.catalog import Catalog, load_catalog


def test_catalog_loads_and_is_unique():
    tools = load_catalog()
    assert len(tools) >= 100
    names = [t.name for t in tools]
    assert len(names) == len(set(names))


def test_catalog_meta_flags():
    cat = Catalog.load()
    delete = cat.get("files.delete")
    assert delete is not None
    assert delete.read_only is False
    search = cat.get("files.search")
    assert search.read_only is True
    # exactly one seeded distractor (notes.create)
    assert sum(1 for t in cat.tools if t.distractor) == 1


def test_to_openai_shape():
    cat = Catalog.load()
    t = cat.get("system.set_volume")
    schema = t.to_openai()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "system.set_volume"
    assert "level" in schema["function"]["parameters"]["properties"]
