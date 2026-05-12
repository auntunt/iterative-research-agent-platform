const navSearch = document.querySelector("#navSearch");
const navLinks = Array.from(document.querySelectorAll("[data-doc-link]"));
const sections = Array.from(document.querySelectorAll(".doc-section"));
const outlineDocs = Array.from(document.querySelectorAll("[data-outline-doc]"));
const outlineLinks = Array.from(document.querySelectorAll(".outline-nav a[href^='#']"));
const mobileToc = document.querySelector(".mobile-toc");

function setActiveDoc(id) {
  navLinks.forEach((link) => link.classList.toggle("active", link.dataset.docLink === id));
  outlineDocs.forEach((doc) => doc.classList.toggle("active", doc.dataset.outlineDoc === id));
}

function setActiveHeading() {
  const visible = sections
    .map((section) => ({ id: section.id, top: Math.abs(section.getBoundingClientRect().top - 24) }))
    .sort((a, b) => a.top - b.top)[0];

  if (visible) setActiveDoc(visible.id);

  let current = "";
  document.querySelectorAll(".doc-section h1, .doc-section h2, .doc-section h3").forEach((heading) => {
    if (heading.getBoundingClientRect().top < 120) current = heading.id;
  });

  outlineLinks.forEach((link) => link.classList.toggle("active", link.getAttribute("href") === `#${current}`));
}

navSearch?.addEventListener("input", (event) => {
  const term = event.target.value.trim().toLowerCase();
  navLinks.forEach((link) => {
    const match = !term || link.textContent.toLowerCase().includes(term);
    link.style.display = match ? "block" : "none";
  });
});

navLinks.forEach((link) => {
  link.addEventListener("click", () => {
    document.body.classList.remove("nav-open");
  });
});

mobileToc?.addEventListener("click", () => {
  document.body.classList.toggle("nav-open");
});

document.addEventListener("click", (event) => {
  if (!document.body.classList.contains("nav-open")) return;
  if (event.target.closest(".sidebar") || event.target.closest(".mobile-toc")) return;
  document.body.classList.remove("nav-open");
});

window.addEventListener("scroll", setActiveHeading, { passive: true });
window.addEventListener("hashchange", setActiveHeading);
setActiveHeading();
