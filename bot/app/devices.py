"""Четыре платформы и их приложения.

Схемы deep-link'ов взяты из дефолтного конфига страницы подписки Remnawave
(subscription-page-configs), а не придуманы: если апстрим их поменяет, менять
надо и здесь.

Happ есть на всех четырёх платформах — он основной. Вторым идёт запасной
клиент на случай, если Happ не ставится или не нравится.
"""

from dataclasses import dataclass
from urllib.parse import quote


@dataclass(frozen=True)
class App:
    name: str
    install_url: str
    # {sub} — ссылка на подписку, {user} — имя пользователя
    deep_link: str

    def link_for(self, sub_url: str, username: str) -> str:
        return self.deep_link.format(sub=sub_url, user=quote(username))


@dataclass(frozen=True)
class Platform:
    key: str
    title: str
    emoji: str
    apps: tuple[App, ...]

    @property
    def primary(self) -> App:
        return self.apps[0]


HAPP_IOS = App(
    name='Happ',
    install_url='https://apps.apple.com/app/happ-proxy-utility/id6783623643',
    deep_link='happ://add/{sub}',
)
HAPP_ANDROID = App(
    name='Happ',
    install_url='https://play.google.com/store/apps/details?id=com.happproxy',
    deep_link='happ://add/{sub}',
)
HAPP_WINDOWS = App(
    name='Happ',
    install_url='https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe',
    deep_link='happ://add/{sub}',
)
HAPP_MACOS = App(
    name='Happ',
    install_url='https://apps.apple.com/app/happ-proxy-utility/id6783623643',
    deep_link='happ://add/{sub}',
)

PLATFORMS: dict[str, Platform] = {
    'ios': Platform(
        key='ios', title='iPhone / iPad', emoji='🍏',
        apps=(
            HAPP_IOS,
            App(
                name='Streisand',
                install_url='https://apps.apple.com/app/streisand/id6450534064',
                deep_link='streisand://import/{sub}',
            ),
        ),
    ),
    'android': Platform(
        key='android', title='Android', emoji='🤖',
        apps=(
            HAPP_ANDROID,
            App(
                name='v2rayNG',
                install_url='https://github.com/2dust/v2rayNG/releases/latest',
                deep_link='v2rayng://install-config?name={user}&url={sub}',
            ),
        ),
    ),
    'windows': Platform(
        key='windows', title='Windows', emoji='🪟',
        apps=(
            HAPP_WINDOWS,
            App(
                name='Koala Clash',
                install_url='https://github.com/coolcoala/clash-verge-rev-lite/releases/latest/download/Koala.Clash_x64-setup.exe',
                deep_link='koala-clash://install-config?url={sub}',
            ),
        ),
    ),
    'macos': Platform(
        key='macos', title='macOS', emoji='🍎',
        apps=(
            HAPP_MACOS,
            App(
                name='Koala Clash',
                install_url='https://github.com/coolcoala/clash-verge-rev-lite/releases/latest/download/Koala.Clash_aarch64.dmg',
                deep_link='koala-clash://install-config?url={sub}',
            ),
        ),
    ),
}

PLATFORM_ORDER = ('ios', 'android', 'windows', 'macos')


def get(key: str) -> Platform | None:
    return PLATFORMS.get(key)
