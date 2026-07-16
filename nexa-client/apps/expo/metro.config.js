/**
 * @type {import('expo/metro-config')}
 */
const { getDefaultConfig } = require('expo/metro-config')

// Expo SDK 54 discovers Yarn workspaces and supplies the complete watch-folder,
// resolver, package-exports, require-context, and minifier configuration.
module.exports = getDefaultConfig(__dirname)
