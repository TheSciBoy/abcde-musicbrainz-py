pkgname=abcde-musicbrainz-py
pkgver=1.0
pkgrel=1
pkgdesc="Perl-free MusicBrainz helper (abcde-musicbrainz-tool replacement) for abcde"
arch=(any)
url="https://abcde.einval.com/"
license=(GPL-3.0-or-later)
depends=(abcde python python-discid)
install=$pkgname.install
source=(abcde-musicbrainz-tool.py)
sha256sums=('44542de7c5350f758ad8bc10a49baa7d501634da42fce0092ff3fe864ea55d86')

package() {
  install -Dm755 abcde-musicbrainz-tool.py "$pkgdir/usr/bin/abcde-musicbrainz-tool-py"
}
