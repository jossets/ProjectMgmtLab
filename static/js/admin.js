(function () {
  const form = document.getElementById('restore-form');
  if (!form) return;

  form.addEventListener('submit', (e) => {
    const input = form.querySelector('input[type="file"]');
    const filename = input && input.files && input.files[0] ? input.files[0].name : 'ce fichier';
    if (!confirm(`Restaurer « ${filename} » va remplacer TOUTES les données actuelles par celles de la sauvegarde. Continuer ?`)) {
      e.preventDefault();
    }
  });
})();
