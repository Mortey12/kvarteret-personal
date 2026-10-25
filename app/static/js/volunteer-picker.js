function registerVolunteerPicker() {
  if (!window.Alpine || window.Alpine.__volunteerPickerRegistered) {
    return;
  }

  window.Alpine.__volunteerPickerRegistered = true;
  Alpine.data("volunteerPicker", ({ searchUrl, minQueryLength = 2, selectionMode = "multiple", initialSelected = [] }) => ({
    searchUrl,
    minQueryLength,
    selectionMode,
    query: "",
    results: [],
    selectedVolunteers: [...initialSelected],
    loading: false,
    errorMessage: "",
    showDropdown: false,
    searchController: null,

    async search() {
      const query = this.query.trim();
      this.errorMessage = "";

      if (query.length < this.minQueryLength) {
        this.cancelSearch();
        this.results = [];
        this.showDropdown = query.length > 0;
        this.loading = false;
        return;
      }

      this.cancelSearch();
      const controller = new AbortController();
      this.searchController = controller;
      this.loading = true;
      this.showDropdown = true;

      try {
        const response = await fetch(`${this.searchUrl}?q=${encodeURIComponent(query)}`, {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
          signal: controller.signal,
        });

        if (!response.ok) {
          throw new Error(`Search failed with status ${response.status}`);
        }

        const payload = await response.json();
        const selectedIds = new Set(this.selectedVolunteers.map((volunteer) => volunteer.volunteer_id));
        this.results = (payload.items || []).filter((item) => !selectedIds.has(item.volunteer_id));
      } catch (error) {
        if (error.name === "AbortError") return;
        this.results = [];
        this.errorMessage = "Kunne ikke hente frivillige.";
      } finally {
        if (this.searchController === controller) {
          this.loading = false;
        }
      }
    },

    async handleEnter() {
      const query = this.query.trim();
      if (query.length < this.minQueryLength) {
        this.showDropdown = query.length > 0;
        return;
      }

      if (this.loading) {
        return;
      }

      if (this.results.length === 0) {
        await this.search();
      }

      if (this.results.length > 0) {
        this.selectVolunteer(this.results[0]);
      }
    },

    selectVolunteer(volunteer) {
      if (this.selectedVolunteers.some((item) => item.volunteer_id === volunteer.volunteer_id)) {
        this.query = "";
        this.results = [];
        this.showDropdown = false;
        return;
      }

      this.selectedVolunteers =
        this.selectionMode === "single" ? [volunteer] : [...this.selectedVolunteers, volunteer];
      this.query = "";
      this.results = [];
      this.showDropdown = false;
      this.errorMessage = "";
      this.$nextTick(() => this.$refs.search?.focus());
    },

    removeVolunteer(volunteerId) {
      this.selectedVolunteers = this.selectedVolunteers.filter((volunteer) => volunteer.volunteer_id !== volunteerId);
    },

    clearSelected() {
      this.selectedVolunteers = [];
      this.query = "";
      this.results = [];
      this.showDropdown = false;
      this.errorMessage = "";
      this.$nextTick(() => this.$refs.search?.focus());
    },

    closeResults() {
      this.showDropdown = false;
    },

    cancelSearch() {
      if (this.searchController) {
        this.searchController.abort();
        this.searchController = null;
      }
    },
  }));
}

if (window.Alpine) {
  registerVolunteerPicker();
} else {
  document.addEventListener("alpine:init", registerVolunteerPicker, { once: true });
}
