/* Minimal UI interactions only — no business logic */

function toggleNav() {
    var links = document.getElementById('navLinks');
    if (links) {
        links.classList.toggle('open');
    }
}

// Auto-hide flash messages after 5 seconds
document.addEventListener('DOMContentLoaded', function () {
    var flashes = document.querySelectorAll('.flash');
    flashes.forEach(function (flash) {
        setTimeout(function () {
            flash.style.transition = 'opacity 0.5s';
            flash.style.opacity = '0';
            setTimeout(function () {
                flash.remove();
            }, 500);
        }, 5000);
    });
});
