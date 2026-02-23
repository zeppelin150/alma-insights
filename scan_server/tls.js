/**
 * Alma Insights — TLS Certificate Generator
 * Per-run self-signed certificate — never touches disk.
 *
 * HIPAA H1: TLS for all loopback traffic.
 */

'use strict';

const forge = require('node-forge');

function generateSelfSignedCert() {
    const keys = forge.pki.rsa.generateKeyPair(2048);
    const cert = forge.pki.createCertificate();

    cert.publicKey = keys.publicKey;
    cert.serialNumber = '01';
    cert.validity.notBefore = new Date();
    cert.validity.notAfter = new Date();
    cert.validity.notAfter.setFullYear(
        cert.validity.notAfter.getFullYear() + 1
    );

    const attrs = [{ name: 'commonName', value: 'alma-scan-server' }];
    cert.setSubject(attrs);
    cert.setIssuer(attrs);

    // SAN for both 127.0.0.1 and localhost
    cert.setExtensions([{
        name: 'subjectAltName',
        altNames: [
            { type: 7, ip: '127.0.0.1' },
            { type: 2, value: 'localhost' },
        ],
    }]);

    cert.sign(keys.privateKey, forge.md.sha256.create());

    return {
        key: forge.pki.privateKeyToPem(keys.privateKey),
        cert: forge.pki.certificateToPem(cert),
    };
}

module.exports = { generateSelfSignedCert };
