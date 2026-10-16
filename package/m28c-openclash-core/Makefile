include $(TOPDIR)/rules.mk

PKG_NAME:=m28c-openclash-core
PKG_VERSION:=1.19.32
PKG_RELEASE:=1
PKG_LICENSE:=GPL-3.0-only

include $(INCLUDE_DIR)/package.mk

define Package/m28c-openclash-core
  SECTION:=net
  CATEGORY:=Network
  TITLE:=Verified Mihomo ARM64 core for OpenClash
  URL:=https://github.com/MetaCubeX/mihomo
  DEPENDS:=@aarch64 +luci-app-openclash
endef

define Package/m28c-openclash-core/description
  Official Mihomo ARM64 release verified against sources.lock.json by
  scripts/prepare-openclash-core.py before package metadata is generated.
  Includes the executable and public source metadata, without subscriptions.
endef

define Build/Prepare
	$(INSTALL_DIR) $(PKG_BUILD_DIR)
	$(CP) ./files/clash_meta $(PKG_BUILD_DIR)/clash_meta
	$(CP) ./files/source.json $(PKG_BUILD_DIR)/source.json
endef

define Build/Configure
endef

define Build/Compile
endef

define Package/m28c-openclash-core/install
	$(INSTALL_DIR) $(1)/etc/openclash/core $(1)/usr/share/m28c-openclash-core
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/clash_meta $(1)/etc/openclash/core/clash_meta
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/source.json $(1)/usr/share/m28c-openclash-core/source.json
endef

$(eval $(call BuildPackage,m28c-openclash-core))
