//v.4
const SteamUser = require('steam-user');
const SteamCommunity = require('steamcommunity');
const SteamTotp = require('steam-totp');
const TradeOfferManager = require('steam-tradeoffer-manager');

const args = process.argv;

if (args.length < 7) {
    process.exit(0);
}

const login = args[2];
const password = args[3];
const shared_secret = args[4];
const identity_secret = args[5];
const tradeOfferLink = args[6];

let inventoryString = "730/2";

if (args[7]) {
    inventoryString = args[7];
}

function sendTrade() {
    const client = new SteamUser();
    const manager = new TradeOfferManager({
        "steam": client,
        "language": "en",
        "pollInterval": 5000
    });
    const community = new SteamCommunity();

    const logOnOptions = {
        "accountName": login,
        "password": password,
        "twoFactorCode": SteamTotp.getAuthCode(shared_secret)
    };

    client.logOn(logOnOptions);

    client.on('loggedOn', function() {
        console.log("Logged into Steam");
    });

    client.on('error', function(err) {
        errorHandler("Steam login error", err);
    });

    client.on('webSession', function(sessionID, cookies) {
        manager.setCookies(cookies, function(err) {
            errorHandler("Something went wrong while setting webSession", err);

            const inventories = inventoryString.split(',');

            const csgoInventory = inventories.filter(inv => inv === '730/2');
            const otherInventories = inventories.filter(inv => inv !== '730/2');

            let completedTrades = 0;
            const totalTrades = (csgoInventory.length > 0 ? 1 : 0) + (otherInventories.length > 0 ? 1 : 0);

            if (totalTrades === 0) {
                console.log("No valid inventories provided");
                process.exit(-1);
            }
            function sendTradeForInventories(inventoriesToSend, tradeName) {
                let offer = manager.createOffer(tradeOfferLink);
                let totalItemsCount = 0;
                let processedInventories = 0;
                let validInventories = 0;

                inventoriesToSend.forEach(inventoryPair => {
                    const [appId, contextId] = inventoryPair.split('/');

                    manager.getInventoryContents(
                        appId,
                        contextId,
                        true,
                        function(err, inventoryItems) {
                            processedInventories++;

                            if (err) {
                                console.log(`Inventory ${appId}:${contextId} is invalid or inaccessible: ${err.message}`);
                            } else {
                                const itemsCount = inventoryItems.length;
                                totalItemsCount += itemsCount;

                                inventoryItems.forEach(item => {
                                    const tradeItem = {
                                        appid: item.appid,
                                        contextid: item.contextid,
                                        assetid: (item.id || item.assetid).toString()
                                    };
                                    offer.addMyItem(tradeItem);
                                });

                                validInventories++;
                            }

                            if (processedInventories === inventoriesToSend.length) {
                                if (validInventories === 0 || totalItemsCount === 0) {
                                    console.log(`No items to send for ${tradeName} trade (empty inventories)`);
                                    completedTrades++;
                                    if (completedTrades === totalTrades) {
                                        process.exit(1);
                                    }
                                    return;
                                }

                                console.log(`${tradeName}: Total ${totalItemsCount} items to send from ${validInventories} valid inventories`);

                                offer.send(function(err, status) {
                                    errorHandler(`${tradeName} Something went wrong while sending trade offer`, err);

                                    if (status === 'pending') {
                                        console.log(`${tradeName} Offer #${offer.id} sent, but requires confirmation`);
                                        community.acceptConfirmationForObject(identity_secret, offer.id, function(err) {
                                            if (!err) {
                                                console.log(`${tradeName} Offer confirmed`);
                                                completedTrades++;
                                                if (completedTrades === totalTrades) {
                                                    process.exit(1);
                                                }
                                            } else if (err && err.toString().includes("Could not act on confirmation")) {
                                                manager._community.httpRequestPost('https://steamcommunity.com//trade/new/acknowledge', {
                                                    "headers": {
                                                        "referer": "https://steamcommunity.com"
                                                    },
                                                    "json": true,
                                                    "form": {
                                                        "sessionid": manager._community.getSessionID(),
                                                        "message": 1
                                                    },
                                                    "checkJsonError": false,
                                                    "checkHttpError": false
                                                }, (err, response, body) => {
                                                    if (response.statusCode === 200) {
                                                        community.acceptConfirmationForObject(identity_secret, offer.id, function(err) {
                                                            if (!err) {
                                                                console.log(`${tradeName} Offer confirmed after HTTP request`);
                                                                completedTrades++;
                                                                if (completedTrades === totalTrades) {
                                                                    process.exit(1);
                                                                }
                                                            } else {
                                                                errorHandler(`${tradeName} Something went wrong during trade confirmation`, err);
                                                            }
                                                        });
                                                    } else {
                                                        errorHandler(`${tradeName} Something went wrong during trade confirmation`, err);
                                                    }
                                                });
                                            } else {
                                                errorHandler(`${tradeName} Something went wrong during trade confirmation`, err);
                                            }
                                        });
                                    } else {
                                        console.log(`${tradeName} Offer #${offer.id} sent successfully`);
                                        completedTrades++;
                                        if (completedTrades === totalTrades) {
                                            process.exit(1);
                                        }
                                    }
                                });
                            }
                        }
                    );
                });
            }

            if (csgoInventory.length > 0) {
                sendTradeForInventories(csgoInventory, "CS:GO");
            }
            if (otherInventories.length > 0) {
                sendTradeForInventories(otherInventories, "Other");
            }
        });

        community.setCookies(cookies);
    });
}

function errorHandler(message, error) {
    if (error) {
        console.log(`HandleError ${message} ${error}`);
        process.exit(-1);
    }
}

sendTrade();