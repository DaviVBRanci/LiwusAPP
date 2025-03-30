self.addEventListener('push', function(event) {
    const options = {
        body: event.data.text(),
        icon: 'https://monica.im/share/artifact?id=VRDzvnhR67ALxb5TyFTd2e', // Substitua pela URL do ícone gerado
        badge: 'https://monica.im/share/artifact?id=euV9uNs9aXxrvedUacUiNm' // Substitua pela URL da badge gerada
    };

    event.waitUntil(
        self.registration.showNotification('Nova Mensagem', options)
    );
});
