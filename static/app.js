const SELF_CHECKS = {
  stage: [
    "Verify stage ribbon cable is fully seated (no bent pins).",
    "Home the stage from the software menu, then retry movement.",
    "Check that limit switches are not obstructed by debris."
  ],
  focus: [
    "Re-seat the objective and confirm it is fully threaded.",
    "Clean the lens with a lint-free wipe (do not use solvent).",
    "Run software autofocus calibration."
  ],
  illumination: [
    "Confirm the LED power connector is firmly attached.",
    "Check the power supply voltage at the barrel jack.",
    "Toggle illumination via the software test menu."
  ],
  pi_camera: [
    "Check the Pi power LED and camera ribbon orientation.",
    "Verify the camera is detected in the OS (vcgencmd get_camera).",
    "Try a known-good power supply (5V/3A min)."
  ],
  physical: [
    "Photograph the damage before moving the unit.",
    "Do not attempt to bend or repair structural parts.",
    "Note whether damage occurred during transport (photo the box)."
  ]
};

const form = document.getElementById("issue-form");
if (form) {
  const cat = document.getElementById("category");
  const box = document.getElementById("self-check");

  function renderChecks() {
    const list = SELF_CHECKS[cat.value] || [];
    box.innerHTML = "<strong>Quick self-checks before submitting:</strong><ul>" +
      list.map(c => "<li>" + c + "</li>").join("") + "</ul>";
  }
  cat.addEventListener("change", renderChecks);
  renderChecks();

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const payload = {
      unitId: Number(form.dataset.unitId),
      customerId: Number(form.dataset.customerId),
      category: fd.get("category"),
      severity: fd.get("severity"),
      description: fd.get("description"),
      photoUrls: []
    };
    const res = await fetch("/api/issues", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      alert("Issue reported. Our team will be in touch.");
      location.reload();
    } else {
      alert("Failed to submit. Check the server console.");
    }
  });
}
