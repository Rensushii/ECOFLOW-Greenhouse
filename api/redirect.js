const https = require('https');

const GIST_RAW_URL = 'https://gist.githubusercontent.com/Rensushii/02945cbdc4abe5148470106e8a8648b8/raw/tunnel_url.txt';

module.exports = (req, res) => {
  https.get(GIST_RAW_URL, (gistRes) => {
    let data = '';
    gistRes.on('data', (chunk) => { data += chunk; });
    gistRes.on('end', () => {
      const tunnelUrl = data.trim();
      if (tunnelUrl.startsWith('https://')) {
        res.writeHead(302, { Location: tunnelUrl });
        res.end();
      } else {
        res.status(200).send('<h1>Greenhouse dashboard is offline. Please try again shortly.</h1>');
      }
    });
  }).on('error', () => {
    res.status(200).send('<h1>Greenhouse dashboard is offline. Please try again shortly.</h1>');
  });
};
