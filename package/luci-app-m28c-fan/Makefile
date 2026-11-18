include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-m28c-fan
PKG_VERSION:=2026.11.16
PKG_RELEASE:=3.m28c1
PKG_LICENSE:=Apache-2.0
PKG_MAINTAINER:=OpenWrt LuCI community

include $(INCLUDE_DIR)/package.mk

# Preserve original runtime files and the declared UI text derivative exactly;
# do not run LuCI's JS minifier or version substitution again.
define Package/luci-app-m28c-fan
  SECTION:=luci
  CATEGORY:=LuCI
  SUBMENU:=3. Applications
  TITLE:=MangoPi M28C PWM fan control
  VERSION:=2026.11.16-r3.m28c1
  PKGARCH:=all
  DEPENDS:=+libc +luci-base
  PROVIDES:=luci-app-m28c-fan-any
endef

define Package/luci-app-m28c-fan/description
  User-supplied M28C PWM fan controller and Chinese LuCI settings page.
  Original package identity and payload hashes are recorded in SOURCE.json.
  UI-CHANGES.json records the deterministic status-text-only derivative.
endef

define Package/luci-app-m28c-fan/conffiles
/etc/config/m28c_fan
endef

define Build/Prepare
	$(INSTALL_DIR) $(PKG_BUILD_DIR)
endef

define Build/Configure
endef

define Build/Compile
endef

define Package/luci-app-m28c-fan/install
	$(INSTALL_DIR) $(1)/etc/config $(1)/etc/init.d $(1)/etc/uci-defaults $(1)/usr/sbin
	$(INSTALL_DIR) $(1)/usr/share/luci/menu.d $(1)/usr/share/rpcd/acl.d
	$(INSTALL_DIR) $(1)/www/luci-static/resources/view/m28c-fan
	$(INSTALL_DATA) ./files/etc/config/m28c_fan $(1)/etc/config/m28c_fan
	$(INSTALL_BIN) ./files/etc/init.d/m28c-fan $(1)/etc/init.d/m28c-fan
	$(INSTALL_BIN) ./files/etc/uci-defaults/90-m28c-fan $(1)/etc/uci-defaults/90-m28c-fan
	$(INSTALL_BIN) ./files/usr/sbin/m28c-fan-control $(1)/usr/sbin/m28c-fan-control
	$(INSTALL_DATA) ./files/usr/share/luci/menu.d/luci-app-m28c-fan.json $(1)/usr/share/luci/menu.d/luci-app-m28c-fan.json
	$(INSTALL_DATA) ./files/usr/share/rpcd/acl.d/luci-app-m28c-fan.json $(1)/usr/share/rpcd/acl.d/luci-app-m28c-fan.json
	$(INSTALL_DATA) ./files/www/luci-static/resources/view/m28c-fan/settings.js $(1)/www/luci-static/resources/view/m28c-fan/settings.js
endef

# Preserve the supplied package's live-install cache refresh. Image building
# only stages the files; it must never reload the build host's rpcd service.
define Package/luci-app-m28c-fan/postinst
[ -n "$${IPKG_INSTROOT}" ] || { rm -f /tmp/luci-indexcache.*
	rm -rf /tmp/luci-modulecache/
	/etc/init.d/rpcd reload 2>/dev/null
	exit 0
}
endef

$(eval $(call BuildPackage,luci-app-m28c-fan))
