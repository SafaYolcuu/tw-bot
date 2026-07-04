/**
 * Map Coord Picker — tw-bot entegrasyonu (RedAlert Map Coord Picker v2.2 tabanlı, sadeleştirilmiş)
 * Yalnızca screen=map. «Bot'a aktar» → twMapCoordBridge.setCoords
 */
(function () {
    'use strict';

    if (typeof game_data === 'undefined' || !game_data || game_data.screen !== 'map') {
        window.alert('Koordinat seçici yalnızca harita (map) ekranında çalışır.');
        return;
    }
    if (typeof TWMap === 'undefined' || !TWMap || !TWMap.mapHandler) {
        window.alert('Harita (TWMap) henüz yüklenmedi; sayfayı yenileyin.');
        return;
    }
    if (window.__twMapCoordPickerLoaded) {
        var old = document.getElementById('ra-map-coord-picker');
        if (old) old.remove();
    }
    window.__twMapCoordPickerLoaded = true;

    var selectedVillages = [];
    var mapOverlay = TWMap;

    function coordFromVillage(v) {
        if (!v) return null;
        var vXY = '' + v.xy;
        return vXY.slice(0, 3) + '|' + vXY.slice(3, 6);
    }

    function refreshList() {
        jQuery('#twMapCoordList').val(selectedVillages.join(' '));
        jQuery('#twMapCoordCount').text(selectedVillages.length);
    }

    function pushToBot() {
        var text = selectedVillages.join(' ');
        if (!text) {
            if (typeof UI !== 'undefined' && UI.ErrorMessage) {
                UI.ErrorMessage('Seçili köy yok.', 3000);
            }
            return;
        }
        if (window.twMapCoordBridge && typeof window.twMapCoordBridge.setCoords === 'function') {
            try {
                window.twMapCoordBridge.setCoords(text);
                if (typeof UI !== 'undefined' && UI.SuccessMessage) {
                    UI.SuccessMessage("Bot'a aktarıldı (Fake planı hedefleri).", 3500);
                }
                return;
            } catch (e) {
                console.warn('[map-coord-picker] bridge', e);
            }
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text);
            if (typeof UI !== 'undefined' && UI.SuccessMessage) {
                UI.SuccessMessage('Panoya kopyalandı (köprü yok).', 3000);
            }
        } else {
            jQuery('#twMapCoordList').select();
            document.execCommand('copy');
            if (typeof UI !== 'undefined' && UI.SuccessMessage) {
                UI.SuccessMessage('Kopyalandı.', 3000);
            }
        }
    }

    function buildUI() {
        var id = 'ra-map-coord-picker';
        var html =
            '<div class="ra-fixed-widget" id="' +
            id +
            '" style="position:fixed;top:10vw;right:10vw;z-index:99999;border:2px solid #7d510f;border-radius:10px;padding:10px;width:340px;max-height:85vh;overflow:auto;background:#e3d5b3 url(\'/graphic/index/main_bg.jpg\') top right repeat;">' +
            '<a class="popup_box_close" href="#" id="twMapCoordClose" style="position:absolute;right:6px;top:4px;">&nbsp;</a>' +
            '<h3 style="margin:0 0 8px 0;font-size:14px;">Harita — koordinat seçici</h3>' +
            '<label style="display:block;font-weight:600;margin-bottom:4px;">Seçili: <span id="twMapCoordCount">0</span></label>' +
            '<textarea id="twMapCoordList" rows="5" style="width:100%;resize:vertical;font-family:Consolas,monospace;font-size:11px;"></textarea>' +
            '<div style="margin-top:8px;display:flex;flex-wrap:wrap;gap:6px;">' +
            '<a href="#" class="btn" id="twMapCoordReset">Sıfırla</a>' +
            '<a href="#" class="btn" id="twMapCoordCopy">Kopyala</a>' +
            '<a href="#" class="btn btn-confirm-yes" id="twMapCoordBot">Bot\'a aktar</a>' +
            '</div>' +
            '<p style="margin:8px 0 0;font-size:10px;color:#444;">Haritada köye tıklayın (tekrar tık = kaldır). Bot → Operasyon → Fake planı.</p>' +
            '</div>';
        jQuery('#contentContainer').prepend(html);
        if (jQuery('#mobileContent').length) jQuery('#mobileContent').prepend(html);

        jQuery('#twMapCoordClose').on('click', function (e) {
            e.preventDefault();
            jQuery('#' + id).remove();
            if (mapOverlay.mapHandler._spawnSector) {
                TWMap.mapHandler.spawnSector = mapOverlay.mapHandler._spawnSector;
            }
            TWMap.map._handleClick = mapOverlay.map._DShandleClick;
            window.__twMapCoordPickerLoaded = false;
            TWMap.reload();
        });

        jQuery('#twMapCoordReset').on('click', function (e) {
            e.preventDefault();
            selectedVillages = [];
            refreshList();
            TWMap.reload();
            if (typeof UI !== 'undefined' && UI.SuccessMessage) {
                UI.SuccessMessage('Seçim temizlendi.', 2500);
            }
        });

        jQuery('#twMapCoordCopy').on('click', function (e) {
            e.preventDefault();
            var c = jQuery('#twMapCoordList').val().trim();
            if (!c) {
                if (typeof UI !== 'undefined' && UI.ErrorMessage) UI.ErrorMessage('Kopyalanacak bir şey yok.', 3000);
                return;
            }
            jQuery('#twMapCoordList').select();
            document.execCommand('copy');
            if (typeof UI !== 'undefined' && UI.SuccessMessage) UI.SuccessMessage('Kopyalandı!', 3000);
        });

        jQuery('#twMapCoordBot').on('click', function (e) {
            e.preventDefault();
            pushToBot();
        });

        if (!jQuery('#' + id).draggable) {
            try {
                jQuery('#' + id).draggable({ cancel: 'textarea, input, .btn' });
            } catch (e2) {}
        }
    }

    mapOverlay.mapHandler._spawnSector = mapOverlay.mapHandler.spawnSector;
    TWMap.mapHandler.spawnSector = function (data, sector) {
        mapOverlay.mapHandler._spawnSector(data, sector);
        var beginX = sector.x - data.x;
        var endX = beginX + mapOverlay.mapSubSectorSize;
        var beginY = sector.y - data.y;
        var endY = beginY + mapOverlay.mapSubSectorSize;
        for (var x in data.tiles) {
            x = parseInt(x, 10);
            if (x < beginX || x >= endX) continue;
            for (var y in data.tiles[x]) {
                y = parseInt(y, 10);
                if (y < beginY || y >= endY) continue;
                var xCoord = data.x + x;
                var yCoord = data.y + y;
                var v = mapOverlay.villages[xCoord * 1000 + yCoord];
                if (v && selectedVillages.length) {
                    var vCoords = coordFromVillage(v);
                    if (vCoords && selectedVillages.indexOf(vCoords) >= 0) {
                        jQuery('#map_village_' + v.id).css({
                            filter: 'brightness(200%) grayscale(100%)',
                        });
                    }
                }
            }
        }
    };

    mapOverlay.map._DShandleClick = mapOverlay.map._handleClick;
    TWMap.map._handleClick = function (e) {
        var pos = this.coordByEvent(e);
        var coord = pos.join('|');
        var village = TWMap.villages[pos[0] * 1000 + pos[1]];
        if (village && village.id) {
            var cur = jQuery('#twMapCoordList').val();
            if (selectedVillages.indexOf(coord) < 0) {
                selectedVillages.push(coord);
                jQuery('#map_village_' + village.id).css({
                    filter: 'brightness(200%) grayscale(100%)',
                });
            } else {
                selectedVillages = selectedVillages.filter(function (c) {
                    return c !== coord;
                });
                jQuery('#map_village_' + village.id).css({ filter: 'none' });
            }
            refreshList();
        }
        return false;
    };

    function ensureBridge(cb) {
        if (window.twMapCoordBridge && typeof window.twMapCoordBridge.setCoords === 'function') {
            cb();
            return;
        }
        if (!window.qt || !window.qt.webChannelTransport) {
            setTimeout(function () {
                ensureBridge(cb);
            }, 40);
            return;
        }
        if (window.__twMapCoordBridgeReady) {
            cb();
            return;
        }
        var s = document.createElement('script');
        s.src = 'qrc:///qtwebchannel/qwebchannel.js';
        s.onload = function () {
            new QWebChannel(qt.webChannelTransport, function (ch) {
                if (ch.objects.twMapCoordBridge) window.twMapCoordBridge = ch.objects.twMapCoordBridge;
                if (ch.objects.twPlannerBridge) window.twPlannerBridge = ch.objects.twPlannerBridge;
                window.__twMapCoordBridgeReady = 1;
                cb();
            });
        };
        (document.head || document.documentElement).appendChild(s);
    }

    buildUI();
    refreshList();
    ensureBridge(function () {});
})();
