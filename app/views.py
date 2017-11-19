# -*- coding: utf-8 -*-
from flask import render_template, flash, redirect, url_for, session, request, g, send_from_directory, send_file, Response, abort, safe_join, make_response
from flask.ext.login import login_user, logout_user, current_user, login_required
from sqlalchemy import exc
from app import app, db, lm
from .forms import LoginForm, SignupForm, ModifyForm, PasswordForm
from .models import User, Movie
from functools import wraps
from flask.ext.wtf import Form
from wtforms import TextField
import os, glob, formic, urllib, base64, json, zlib
from config import CONVERT_CORES, VIDEO_FOLDER, ROTTEN_KEY
from werkzeug.security import generate_password_hash, check_password_hash

def is_moderator():
    """Return true if user level is greater than zero"""
    try:
        role = User.query.filter_by(id = g.user.id).first().role
        if role > 0:
            return True
        else:
            return False
    # Catch exception when not logged in
    except AttributeError:
        return False

def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        role = g.user.role
        if role not in [1, 2]:
            flash('Invalid permissions.')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

@lm.user_loader
def load_user(user_id):
    user_id = User.query.get(user_id)
    return user_id

@app.before_request
def before_request():
    g.user = current_user
    g.user.is_moderator = is_moderator()

@app.route('/setup', methods = ['GET', 'POST'])
def setup():
    """Setup page, only accessible if no users exist"""
    # Try if database exists
    try:
        # If no user found in database, show form
        if db.session.query(User).count() < 1:
            form = SignupForm()
            # Passed form validation? continue
            if form.validate_on_submit():
                # Create user object and add it to database
                user = User(username = form.username.data, password = generate_password_hash(form.password.data), role = 2)
                db.session.add(user)
                db.session.commit()
                flash('Account created! You are now logged in!')
                # Log new user in
                login_user(user)
                return redirect(url_for('index'))
            return render_template('signup.html', form = form)
        else:
            flash('Setup already completed.')
            return redirect(url_for('index'))
    # If not, let's create the database:
    except exc.OperationalError:
        db.create_all()
        return redirect(url_for('setup'))

@app.route('/')
@app.route('/index')
@login_required
def index():
    """Lists movies with status of 2 (ready for viewing)"""
    sort = request.args.get('sort')
    way = request.args.get('way')
    if way == '1':
        way = 'desc'
    else:
        way = 'asc'
    if sort == 'rating':
        movies = ['ratings {!s}'.format(way)]
    elif sort == 'name':
        movies = ['name {!s}'.format(way)]
    else:
        movies = ['name']
    movies = Movie.query.filter_by(status = 2).order_by(*movies).all()
    for movie in movies:
        movie.genres = movie.genres.split(', ')
    new_movies = Movie.query.filter_by(status = 1).all()
    return render_template('movies.html', movies = movies, new_movies = new_movies)

@app.route('/genre/<search_tag>')
@login_required
def genre(search_tag):
    """Search function by tags"""
    # Get all movies
    movies = Movie.query.filter_by(status = 2).all()
    hits = []
    # Loop through them
    for movie in movies:
        taglist = []
        # Split them into list
        tags = movie.genres.split(', ')
        for tag in tags:
            # If matches search tag:
            if tag == search_tag:
                # Append it to our very own list
                hits.append(movie)
                movie.genres = movie.genres.split(', ')
    return render_template('movies.html', movies = hits, search = search_tag)

@app.route('/movies/watch/<movie_id>')
@login_required
def movie(movie_id):
    """Fetch movie or 404"""
    movie = Movie.query.filter_by(id = movie_id).first_or_404()
    return render_template('index.html', movie = movie)

@app.route('/subtitles/')
@login_required
def subtitles():
    """Re-encode subtitles to UTF-8 for scandinavian letters to work"""
    movie = request.args.get('movie')
    videofolder = VIDEO_FOLDER
    path = safe_join(videofolder, movie)
    if movie.endswith('.srt'):
        filename = safe_join(videofolder, movie)
        with open(filename, 'r') as fd:
            movie = fd.read()
        movie = movie.decode('iso-8859-1').encode('utf-8')
        return Response(movie, mimetype='text/plain')
    else:
        flash('Not a subtitle file.')
        return redirect(url_for('index'))

@app.route('/videos/<movie>')
@login_required
def video(movie):
    """Find the correct movie file and serve it using X-Accel-Redirect"""
    # Movie name is just ID.extension so we can split it with dot
    split = movie.split('.')
    movie_id = split[0]
    extension = split[1]
    movie = Movie.query.filter_by(id = movie_id).first()
    # Remove extensions from urls fetched from database
    url = movie.url
    url = url.replace('.mp4','')
    url = url.replace('.avi','')
    url = url.replace('.mkv','')
    # Append correct extension to it
    redirect_path = '/raw_videos/{!s}.{!s}'.format(url, extension)

    response = make_response('')
    # Generate proper mimetypes
    if extension == 'webm':
        mimetype = 'video/webm'
    elif extension == 'mkv':
        mimetype = 'video/h264'
    elif extension == 'mp4':
        mimetype = 'video/mp4'
    # Let HTTPd serve the file
    response.headers['Content-Type'] = mimetype
    response.headers['X-Accel-Redirect'] = redirect_path
    return response

@app.route('/modify', methods = ['GET', 'POST'])
@login_required
def modify():
    """Processes new-found movie form data and adds it to database"""
    form = ModifyForm()
    if form.validate_on_submit():
        movie = form.name.data
        # Get movie data and put it to our database
        rotten_url = 'http://api.rottentomatoes.com/api/public/v1.0/movies/{!s}.json?apikey={!s}'.format(movie, ROTTEN_KEY)
        req = urllib.Request(rotten_url, headers={'Accept-Encoding': 'gzip, identity'})
        conn = urllib.urlopen(req)
        movie = conn.read()
        try:
            if conn.headers['content-encoding'] == 'gzip':
                movie = zlib.decompress(movie, 16+zlib.MAX_WBITS)
        except KeyError:
            pass
        movie = json.loads(movie)
        description = movie['synopsis']
        genres = movie['genres']
    
        # Put all genres in a string
        final = ""
        counter = 1
        for genre in genres:
            final += genre
            if len(genres) > 1 and counter < len(genres):
                final += ', '
            counter += 1
        genres = final

        # Rest of the stuff
        ratings = movie['ratings']
        ratings = ratings.get('audience_score')
        posters = movie['posters']
        posters = posters.get('thumbnail')
        movie = movie['title']
        ticketid = form.id.data
    
        # Just dump it to database
        querymovie = Movie.query.filter_by(id = ticketid).first()
        querymovie.status = 2
        querymovie.description = description
        querymovie.genres = genres
        querymovie.ratings = ratings
        querymovie.posters = posters
        querymovie.name = movie
        db.session.commit()
        flash(movie+' added!')

    items = Movie.query.filter_by(status = 1).all()
    return render_template('modify.html', form = form, items = items)

@app.route('/profile', methods = ['GET', 'POST'])
@login_required
def profile():
    """Profile page with ability to change password"""
    form = PasswordForm()
    # Form submitted?
    if form.validate_on_submit():
        # Fetch current user's data
        user_data = User.query.filter_by(id = g.user.id).first()
        # Check if old password was correct
        if check_password_hash(user_data.password, form.password.data):
            # Generate new password
            user_data.password = generate_password_hash(form.newpassword.data)
            # Done, commit to database
            db.session.commit()
            flash('Password changed!')
            return redirect(url_for('profile'))
    return render_template('profile.html', form = form)
  
@app.route('/signup', methods = ['GET', 'POST'])
@admin_required
def signup():
    """Register page. Only admin/moderator can create new users"""
    form = SignupForm()
    # Form validation passed? Add new user.
    if form.validate_on_submit():
        user = User(username = form.username.data, password = generate_password_hash(form.password.data))
        db.session.add(user)
        db.session.commit()
        flash('Account created!')
        login_user(user)
        return redirect(url_for('index'))
    return render_template('signup.html', form = form)

@app.route('/login', methods = ['GET', 'POST'])
def login():
    """Login page. Checking of password in this method"""
    # Already authenticated? gtfo
    if g.user is not None and g.user.is_authenticated():
        flash('Already logged in.')
        return redirect(url_for('index'))
    form = LoginForm()
    # Passed form validation? let's roll
    if form.validate_on_submit():
        session['remember_me'] = form.remember_me.data
        user = form.username.data
        user_data = User.query.filter_by(username = user).first()
        # User exists
        if user_data:
            # Password matches
            if check_password_hash(user_data.password, form.password.data):
                # Ticket 'remember me'?
                if 'remember_me' in session:
                    remember_me = session['remember_me']
                    session.pop('remember_me', None)
                # All good, let's log user in
                login_user(user_data, remember = remember_me)
                return redirect(request.args.get('next') or url_for('index'))
            else:
                flash('Invalid username or password')
        else:
            flash('Invalid username or password')
    return render_template('login.html', form = form)

@app.route('/logout')
def logout():
    """Just log out the user, no fancy stuff"""
    logout_user()
    return redirect(url_for('index'))

@app.route('/admin/<what>/<int:who>')
@app.route('/admin/')
@admin_required
def admin(what = None, who = None):
    """Admin area with moderation functions"""
    if who == 1:
        flash('Deleting of admin account is not possible.')
        return redirect(url_for('admin'))
    if what is None and who is None:
        users = User.query.all()
        return render_template('admin.html', users = users)
    else:
        user = User.query.filter_by(id = who).first()
        name = user.username
        if what == 'delete':
            db.session.delete(user)
            message = 'User {!s} deleted.'.format(name)
        elif what == 'promote':
            user.role = 1
            message = 'User {!s} promoted to moderator.'.format(name)
        elif what == 'demote':
            user.role = 0
            message = 'User {!s} demoted to normal user.'.format(name)
        else:
            return redirect(url_for('admin'))
        db.session.commit()
        flash(message)
    return redirect(url_for('admin'))
