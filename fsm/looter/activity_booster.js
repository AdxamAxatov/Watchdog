//v.1
const SteamUser = require('steam-user');
const SteamTotp = require('steam-totp');
const client = new SteamUser();

const CHECK_INTERVAL = 15 * 1000;

const args = process.argv;

if (args.length <= 2) {
    process.exit(0);
}
var [,, login, password, shared_secret, machineName, APP_IDS, minutes] = process.argv;

const TARGET_MINUTES = parseInt(minutes, 10);

const GAMES_TO_PLAY = APP_IDS.split(',').map(Number);

const logOnOptions = {
    accountName: login,
    password: password,
    twoFactorCode: SteamTotp.getAuthCode(shared_secret),
    machineName: machineName
};

client.logOn(logOnOptions);

client.on('loggedOn', () => {
    client.requestFreeLicense(GAMES_TO_PLAY, (err, grantedPackages, grantedAppIDs) => {
        if (grantedAppIDs.length > 0) {
            console.log('Added games to account:', grantedAppIDs);
        }

        setTimeout(() => {
            console.log(`Starting games activity for ${TARGET_MINUTES} minutes`);
            client.setPersona(SteamUser.EPersonaState.Online);
            client.gamesPlayed(GAMES_TO_PLAY, true);

            const targetTimeMs = TARGET_MINUTES * 60 * 1000;
            let elapsedTimeMs = 0;

            const interval = setInterval(() => {
                elapsedTimeMs += CHECK_INTERVAL;

                if (!client.steamID) {
                    clearInterval(interval);
                    client.logOff();
                    process.exit(1);
                }

                if (elapsedTimeMs >= targetTimeMs) {
                    console.log(`Emulation done!`);
                    client.gamesPlayed([]);
                    clearInterval(interval);
                    client.logOff();
                    process.exit(2);
                }
            }, CHECK_INTERVAL);
        }, 80000);
    });
});

client.on('error', (err) => {
    if (err.eresult) {
        console.error('Error###', err.eresult);
    }

    process.exit(4);
});

client.on('disconnected', (eresult, msg) => {
    console.log('Disconnected from Steam:', msg);
    process.exit(1);
});