"""Tests for HTML template rendering"""

from bs4 import BeautifulSoup


class TestIndexTemplate:
    def test_renders_successfully(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.content_type.startswith("text/html")

    def test_html_structure(self, client):
        resp = client.get("/")
        soup = BeautifulSoup(resp.data, "html.parser")

        # Title
        assert soup.title is not None
        assert "Remote ID Tracker" in soup.title.string

        # Meta viewport
        meta_viewport = soup.find("meta", attrs={"name": "viewport"})
        assert meta_viewport is not None

        # CSP
        meta_csp = soup.find("meta", attrs={"http-equiv": "Content-Security-Policy"})
        assert meta_csp is not None
        assert "default-src 'self'" in meta_csp["content"]

    def test_key_elements_present(self, client):
        resp = client.get("/")
        soup = BeautifulSoup(resp.data, "html.parser")

        assert soup.find(id="sidebar") is not None
        assert soup.find(id="droneList") is not None
        assert soup.find(id="map") is not None
        assert soup.find(id="droneDetail") is not None
        assert soup.find(id="detailUasId") is not None
        assert soup.find(id="detailChart") is not None
        assert soup.find(id="startTime") is not None
        assert soup.find(id="endTime") is not None
        assert soup.find(id="refreshBtn") is not None
        assert soup.find(id="syncToggle") is not None
        assert soup.find(id="showOperators") is not None
        assert soup.find(id="showTracks") is not None
        assert soup.find(id="trackOpacity") is not None
        assert soup.find(id="lastUpdate") is not None
        assert soup.find(id="openSidebar") is not None
        assert soup.find(id="closeSidebar") is not None
        assert soup.find(id="closeDetail") is not None

    def test_cdn_links_present(self, client):
        resp = client.get("/")
        soup = BeautifulSoup(resp.data, "html.parser")

        # Leaflet CSS
        leaflet_css = soup.find("link", href=lambda v: v and "leaflet" in v)
        assert leaflet_css is not None

        # Flatpickr CSS
        flatpickr_css = soup.find("link", href=lambda v: v and "flatpickr" in v)
        assert flatpickr_css is not None

        # Font Awesome CSS
        fa_css = soup.find("link", href=lambda v: v and "font-awesome" in v)
        assert fa_css is not None

    def test_js_scripts_present(self, client):
        resp = client.get("/")
        soup = BeautifulSoup(resp.data, "html.parser")

        scripts = soup.find_all("script")
        srcs = [s.get("src", "") for s in scripts]

        js_files = ["units.js", "api.js", "map.js", "ui.js"]
        for js in js_files:
            assert any(js in src for src in srcs), f"Missing script: {js}"

        # Leaflet JS
        assert any("leaflet" in src for src in srcs)
        # Flatpickr JS
        assert any("flatpickr" in src for src in srcs)

    def test_time_presets(self, client):
        resp = client.get("/")
        soup = BeautifulSoup(resp.data, "html.parser")

        presets = soup.select(".header-time-presets button")
        preset_hours = {b.get("data-hours") for b in presets}
        assert "1" in preset_hours
        assert "6" in preset_hours
        assert "24" in preset_hours
        assert "168" in preset_hours

    def test_sidebar_footer(self, client):
        resp = client.get("/")
        soup = BeautifulSoup(resp.data, "html.parser")

        footer = soup.find("div", class_="sidebar-footer")
        assert footer is not None
        assert footer.find(id="showOperators") is not None
        assert footer.find(id="showTracks") is not None
        assert footer.find(id="trackOpacity") is not None

    def test_data_base_url(self, client):
        resp = client.get("/")
        soup = BeautifulSoup(resp.data, "html.parser")
        body = soup.find("body")
        assert body is not None
        assert "data-base-url" in body.attrs

    def test_favicon(self, client):
        resp = client.get("/")
        soup = BeautifulSoup(resp.data, "html.parser")
        favicon = soup.find("link", rel="icon")
        assert favicon is not None
        assert "favicon.svg" in favicon.get("href", "")
