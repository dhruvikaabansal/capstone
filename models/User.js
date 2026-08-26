const mongoose = require('mongoose');
const bcrypt = require('bcryptjs');

const BCRYPT_ROUNDS = 12;

const userSchema = new mongoose.Schema(
  {
    name: {
      type: String,
      required: [true, 'Name is required'],
      trim: true,
      maxlength: 120,
    },
    email: {
      type: String,
      required: [true, 'Email is required'],
      unique: true,
      lowercase: true,
      trim: true,
      match: [/^[^\s@]+@[^\s@]+\.[^\s@]+$/, 'Enter a valid email address'],
    },
    // Absent for accounts created through Google OAuth.
    password: {
      type: String,
      minlength: [8, 'Password must be at least 8 characters'],
    },
    googleId: {
      type: String,
      index: true,
      sparse: true,
    },
    avatar: {
      type: String,
      default: '',
    },
    lastLoginAt: Date,
  },
  {
    timestamps: true,
    toJSON: {
      transform(doc, ret) {
        delete ret.password;
        delete ret.__v;
        return ret;
      },
    },
  }
);

/**
 * Replace a document's plaintext password with its bcrypt hash.
 * Exported so it can be tested without a database connection.
 */
async function hashPasswordField(doc) {
  if (!doc.password || (doc.isModified && !doc.isModified('password'))) return doc;
  doc.password = await bcrypt.hash(doc.password, BCRYPT_ROUNDS);
  return doc;
}

/** Hash the password whenever it is set or changed. */
userSchema.pre('save', async function hashOnSave() {
  await hashPasswordField(this);
});

/**
 * Check a candidate password against the stored hash.
 * Returns false for accounts that have no password (OAuth-only).
 */
userSchema.methods.comparePassword = async function comparePassword(candidate) {
  if (!this.password || !candidate) return false;
  return bcrypt.compare(candidate, this.password);
};

const User = mongoose.models.User || mongoose.model('User', userSchema);

module.exports = User;
module.exports.hashPasswordField = hashPasswordField;
module.exports.BCRYPT_ROUNDS = BCRYPT_ROUNDS;
