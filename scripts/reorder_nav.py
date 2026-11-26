"""Post-process generated HTML to reorder navigation by frontmatter order.

This script reorders navigation items in generated Zensical/MkDocs documentation
based on the `order` field in markdown frontmatter.

Usage:
    uv run python scripts/reorder_nav.py

How it works:
    1. Scans all .md files in docs/ for `order:` frontmatter values
    2. Groups navigation items by their parent directory
    3. Reorders items within each section based on the order values
    4. Items with lower order values appear first within their section
    5. Items without order values appear at the end in alphabetical order

Frontmatter format:
    ---
    title: My Page
    order: 1
    ---

    Content here...

    Note: Order is relative to files in the same directory.
    For example, docs/getting-started/quickstart.md with order: 1
    appears before docs/getting-started/advanced.md with order: 2

Integration:
    This script is automatically run after `duty build` via duties.py.
    It can also be run manually at any time.

Dependencies:
    - beautifulsoup4
"""

from pathlib import Path
import re
import sys
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup


if TYPE_CHECKING:
    from bs4 import Tag


def extract_frontmatter_order(content: str) -> int | None:
    """Extract order value from markdown frontmatter.

    Args:
        content: Full markdown file content

    Returns:
        Order value as integer, or None if not found
    """
    # Match YAML frontmatter block
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not match:
        return None

    frontmatter = match.group(1)

    # Look for order field (handle various YAML formats)
    order_match = re.search(r"^order:\s*(\d+)\s*$", frontmatter, re.MULTILINE)
    if order_match:
        return int(order_match.group(1))

    return None


def extract_frontmatter_orders(docs_dir: Path) -> dict[str, int]:
    """Extract order values from markdown frontmatter.

    Returns:
        Dictionary mapping relative paths (without .md) to order values
    """
    orders = {}

    for md_file in docs_dir.rglob("*.md"):
        try:
            with md_file.open(encoding="utf-8") as f:
                content = f.read()

            order = extract_frontmatter_order(content)
            if order is not None:
                # Convert path to URL-like key (remove .md, normalize separators)
                rel_path = md_file.relative_to(docs_dir)
                path_key = str(rel_path.with_suffix(""))
                orders[path_key] = order

        except Exception as e:  # noqa: BLE001
            print(f"Error processing {md_file}: {e}")

    return orders


def extract_nav_item_info(nav_item: Tag) -> tuple[str | None, str | None]:
    """Extract path and title from a navigation item.

    Returns:
        Tuple of (relative_path, title) or (None, None) if not found
    """
    # Find the link within the nav item
    link = nav_item.find("a", class_="md-nav__link")
    if not link:
        return None, None

    href = link.get("href", "")
    if not href or href == "":
        # Handle root/index case
        href = "index"
    assert isinstance(href, str)
    # Clean up href - remove leading/trailing slashes and trailing index.html
    href = href.strip("/")
    if href.endswith("/"):
        href = href[:-1]
    if href.endswith("index.html"):
        href = href[:-11] or "index"
    if href.endswith(".html"):
        href = href[:-5]

    # Empty href means root index
    if not href:
        href = "index"

    # Extract title from the span with md-ellipsis class
    title_span = link.find("span", class_="md-ellipsis")
    title = title_span.get_text(strip=True) if title_span else href

    return href, title


def sort_nav_items(nav_items: list[Tag], orders: dict[str, int]) -> list[Tag]:
    """Sort navigation items based on frontmatter order.

    Items are sorted within their parent directory context.
    Works for both md-tabs__item and md-nav__item elements.

    Args:
        nav_items: List of BeautifulSoup Tag objects representing nav items
        orders: Dictionary mapping paths to order values

    Returns:
        Sorted list of nav items
    """
    return sort_nav_items_with_context(nav_items, orders, "")


def sort_nav_items_with_context(
    nav_items: list[Tag], orders: dict[str, int], current_page_path: str
) -> list[Tag]:
    """Sort navigation items based on frontmatter order with context awareness.

    Resolves relative paths (../, ./, etc.) based on the current page location
    to correctly match against the orders dictionary. This ensures navigation
    items are properly sorted even on deeply nested pages where hrefs use
    multiple levels of parent directory references (e.g., ../../).

    Args:
        nav_items: List of BeautifulSoup Tag objects representing nav items
        orders: Dictionary mapping paths to order values
        current_page_path: Current page path for resolving relative links (e.g., "getting-started/quickstart")

    Returns:
        Sorted list of nav items
    """  # noqa: E501

    def get_sort_key(nav_item: Tag) -> tuple[int, str]:
        """Generate sort key for a navigation item."""
        # Handle both md-tabs__item and md-nav__item
        link = nav_item.find(
            "a",
            class_=lambda c: c is not None and ("md-tabs__link" in c or "md-nav__link" in c),
        )
        if not link:
            return (999999, "")

        href = link.get("href", "")
        assert isinstance(href, str)
        # Resolve relative paths based on current page context
        # Use Path-like resolution

        current_parts = Path(current_page_path).parts if current_page_path else ()

        # Clean up href first
        href = href.strip("/")

        # Resolve relative paths based on current page context
        # This handles: "../foo", "./foo", "foo" (all relative to current page)
        # Skip absolute URLs (http://, https://, //)
        if href and not href.startswith(("http://", "https://", "//")):
            # Build absolute path from current location
            resolved_parts = list(current_parts)
            href_parts = Path(href).parts

            for part in href_parts:
                if part == "..":
                    if resolved_parts:
                        resolved_parts.pop()
                elif part == ".":
                    continue
                else:
                    resolved_parts.append(part)

            href = "/".join(resolved_parts) if resolved_parts else ""

        # Extract path from href (remove .html extensions)
        href = href.strip("/")
        if href.endswith("/"):
            href = href[:-1]
        if href.endswith("index.html"):
            href = href[:-11] or "index"
        if href.endswith(".html"):
            href = href[:-5]

        # Empty href means root index
        if not href:
            href = "index"

        # Get title for fallback sorting
        title = link.get_text(strip=True)

        path = href

        # Check for exact match first
        if path in orders:
            return (orders[path], title or "")

        # Check for path variations (with/without index)
        path_variations = [
            path,
            f"{path}/index" if path != "index" else "index",
            path.replace("/index", "") if path.endswith("/index") else f"{path}/index",
        ]

        for variation in path_variations:
            if variation in orders:
                return (orders[variation], title or "")

        # Default order for items without explicit order (put at end, sort alphabetically)
        return (999999, title or path)

    return sorted(nav_items, key=get_sort_key)


def reorder_navigation_in_html(html_file: Path, orders: dict[str, int]) -> bool:
    """Reorder navigation elements in a single HTML file.

    Returns:
        True if any changes were made, False otherwise
    """
    try:
        with html_file.open(encoding="utf-8") as f:
            content = f.read()

        soup = BeautifulSoup(content, "html.parser")
        modified = False

        # Determine current page path for relative link resolution
        # site/getting-started/index.html -> getting-started
        # site/index.html -> ""
        site_dir = html_file.parent
        while site_dir.name != "site" and site_dir.parent != site_dir:
            site_dir = site_dir.parent

        if site_dir.name == "site":
            try:
                current_page_path = str(html_file.parent.relative_to(site_dir))
                if current_page_path == ".":
                    current_page_path = ""
            except ValueError:
                current_page_path = ""
        else:
            current_page_path = ""

        # 1. Reorder tabs navigation (md-tabs__list)
        tabs_container = soup.find("ul", class_="md-tabs__list")
        if tabs_container:
            tab_items = tabs_container.find_all("li", class_="md-tabs__item", recursive=False)
            if len(tab_items) > 1:
                sorted_tabs = sort_nav_items_with_context(tab_items, orders, current_page_path)
                if tab_items != sorted_tabs:
                    modified = True
                    for item in tab_items:
                        item.extract()
                    for item in sorted_tabs:
                        tabs_container.append(item)

        # 2. Reorder sidebar navigation (md-nav__list)
        nav_containers = soup.find_all("ul", class_="md-nav__list")

        for nav_container in nav_containers:
            # Get all direct nav items (not nested ones)
            nav_items = nav_container.find_all("li", class_="md-nav__item", recursive=False)

            if len(nav_items) <= 1:
                continue  # Skip if only one or no items

            # Sort the items with context
            sorted_items = sort_nav_items_with_context(nav_items, orders, current_page_path)

            # Check if order actually changed
            if nav_items != sorted_items:
                modified = True

                # Remove all nav items from the container
                for item in nav_items:
                    item.extract()

                # Add them back in sorted order
                for item in sorted_items:
                    nav_container.append(item)

        # Write back if modified
        if modified:
            with html_file.open("w", encoding="utf-8") as f:
                f.write(str(soup))
            return True

    except Exception as e:  # noqa: BLE001
        print(f"Error processing {html_file}: {e}")

    return False


def main():
    """Main function to reorder navigation in all HTML files."""
    # Determine project root (assumes script is in scripts/ subdirectory)
    script_dir = Path(__file__).parent
    project_root = script_dir.parent

    docs_dir = project_root / "docs"
    site_dir = project_root / "site"

    if not docs_dir.exists():
        print(f"Error: docs directory not found at {docs_dir}")
        return 1

    if not site_dir.exists():
        print(f"Error: site directory not found at {site_dir}")
        return 1

    print("🔍 Extracting frontmatter orders...")
    orders = extract_frontmatter_orders(docs_dir)

    if orders:
        print(f"📋 Found order specifications for {len(orders)} pages:")
        for path, order in sorted(orders.items(), key=lambda x: (x[0].rsplit("/", 1)[0], x[1])):
            print(f"   {order:>4d}: {path}")
    else:
        print("⚠️  No order specifications found in frontmatter")
        return 0

    print("\n🔄 Reordering navigation in HTML files...")
    modified_count = 0

    # Process all HTML files
    for html_file in site_dir.rglob("*.html"):
        if reorder_navigation_in_html(html_file, orders):
            modified_count += 1
            rel_path = html_file.relative_to(site_dir)
            print(f"   ✓ {rel_path}")

    print(f"\n✨ Navigation reordered in {modified_count} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
