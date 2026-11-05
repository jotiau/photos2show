import re
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_setup_scripts_suggest_the_documented_pairs():
    """setup.sh / setup.ps1 copy the Recommended pairs table; a model changed in one place must change in all."""
    docs = (ROOT / "docs/configuration.md").read_text()
    pairs = docs[docs.index("**Recommended pairs**"):docs.index("The two models do not need")]
    documented = set(re.findall(r"`([\w.-]+:[\w.-]+)`", pairs))
    for script in ("setup.sh", "setup.ps1"):
        pattern = r"\$?(?:analysis|slideshow) ?= ?\"?([\w.-]+:[\w.-]+)"
        suggested = set(re.findall(pattern, (ROOT / script).read_text()))
        assert suggested and suggested <= documented, (script, suggested - documented)
