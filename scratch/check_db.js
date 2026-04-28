const mongoose = require('mongoose');
require('dotenv').config();

async function checkDB() {
    try {
        const uri = 'mongodb://localhost:27018/capstone';
        await mongoose.connect(uri);
        console.log('Connected to MongoDB at ' + uri);
        const collections = await mongoose.connection.db.listCollections().toArray();
        console.log('Collections:', collections.map(c => c.name));
        
        for (const col of collections) {
            const count = await mongoose.connection.db.collection(col.name).countDocuments();
            console.log(`Collection ${col.name}: ${count} documents`);
        }
        
        await mongoose.disconnect();
    } catch (err) {
        console.error('Error:', err);
    }
}

checkDB();
