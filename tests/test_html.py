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
        assert "Drone Tracker" in soup.title.string

        # Meta viewport
        meta_viewport = soup.find("meta", attrs={"name": "viewport"})
        assert meta_viewport is not None

        # CSP is set as HTTP response header, not meta tag
        meta_csp = soup.find("meta", attrs={"http-equiv": "Content-Security-Policy"})
        assert meta_csp is None
        assert "Content-Security-Policy" in resp.headers
        assert "default-src 'self'" in resp.headers["Content-Security-Policy"]

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
        assert soup.find(id="remoteDetail") is not None
        assert soup.find(id="showOperators") is not None
        assert soup.find(id="showTracks") is not None
        assert soup.find(id="trackOpacity") is not None
        assert soup.find(id="lastUpdate") is not None
        assert soup.find(id="openSidebar") is not None
        assert soup.find(id="closeSidebar") is not None
        assert soup.find(id="closeDetail") is not None
        assert soup.find(id="showKnownDrones") is not None
        assert soup.find(id="showUnknownDrones") is not None

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

    def test_settings_panel(self, client):
        resp = client.get("/")
        soup = BeautifulSoup(resp.data, "html.parser")

        panel = soup.find("div", class_="settings-panel")
        assert panel is not None
        assert panel.find(id="showOperators") is not None
        assert panel.find(id="showTracks") is not None
        assert panel.find(id="trackOpacity") is not None
        assert panel.find(id="showKnownDrones") is not None
        assert panel.find(id="showUnknownDrones") is not None
        assert soup.find(id="settingsBackdrop") is None
        assert soup.find(id="openSettings") is not None
        assert soup.find(id="closeSettings") is not None

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

    def test_alert_log_modal(self, client):
        resp = client.get("/")
        soup = BeautifulSoup(resp.data, "html.parser")

        # Alert log button in analytics dropdown
        assert soup.find(id="openAlertLogDropdown") is not None
        assert soup.find(id="alertLogModal") is not None
        assert soup.find(id="closeAlertLog") is not None
        assert soup.find(id="alertLogBody") is not None
        assert soup.find(id="alertLogTotal") is not None
        assert soup.find(id="alertLogSearchBtn") is not None
        assert soup.find(id="alertLogUasFilter") is not None
        assert soup.find(id="alertLogGeozoneFilter") is not None

    def test_analytics_dropdown(self, client):
        resp = client.get("/")
        soup = BeautifulSoup(resp.data, "html.parser")

        # Analytics dropdown in header
        assert soup.find(id="analyticsBtn") is not None
        assert soup.find(id="analyticsDropdown") is not None
        header_dropdowns = soup.select(".analytics-dropdown .dropdown-header")
        assert any("Analytics" in d.get_text() for d in header_dropdowns)

        # Stats cards inside dropdown
        assert soup.find(id="statDrones") is not None
        assert soup.find(id="statSessions") is not None
        assert soup.find(id="statPositions") is not None
        assert soup.find(id="statActiveAlerts") is not None
        assert soup.find(id="statTotalAlerts") is not None
        assert len(soup.select(".analytics-stats .stat-card")) == 5

        # Alert log button inside dropdown
        assert soup.find(id="openAlertLogDropdown") is not None
