from wotagent.paths import data_path, project_root


def test_project_paths_are_independent_from_package_depth() -> None:
    root = project_root()

    assert (root / "pyproject.toml").is_file()
    assert data_path("eldercare.db") == root / "data" / "eldercare.db"
    assert "src" not in data_path("eldercare.db").relative_to(root).parts
